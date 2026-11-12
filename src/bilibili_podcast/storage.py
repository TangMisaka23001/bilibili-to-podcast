"""S3 (Cloudflare R2) sync: mirror a local root directory into a bucket.

After sync() returns, the bucket's keys are exactly the local root's relative
file paths. Keys are uploaded when local-only, when their size changed, or when
forced (see ``force_prefixes`` / ``force_keys``); remote-only keys are deleted.
"""
from __future__ import annotations

import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from boto3.session import Session
from botocore.exceptions import ClientError

from bilibili_podcast.logger import get_logger

logger = get_logger()

#: Fallback types for extensions a minimal container's mime.types may not know.
_MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".m4s": "audio/iso.segment",
    ".opus": "audio/ogg",
    ".webp": "image/webp",
}
_IMMUTABLE_SUFFIXES = (".m4a", ".mp3", ".opus", ".jpg", ".png", ".webp")

#: Uploads are network-bound; a small pool keeps a first full sync from
#: serialising thousands of round trips.
_UPLOAD_WORKERS = 8


@dataclass
class SyncResult:
    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def make_s3_client(
    access_key: str,
    secret_key: str,
    endpoint_url: str,
):
    return Session().client(
        service_name="s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint_url,
    )


def _content_type(key: str) -> str:
    ext = os.path.splitext(key)[1].lower()
    return _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(key)[0] or "application/octet-stream"


def _cache_control(key: str) -> str:
    if key.endswith(_IMMUTABLE_SUFFIXES):
        return "public, max-age=31536000, immutable"
    return "max-age=300"


def _upload_args(key: str) -> dict[str, str]:
    return {"ContentType": _content_type(key), "CacheControl": _cache_control(key)}


def _walk_local_keys(local_root: str) -> dict[str, int]:
    """Map every local file's relative key to its size in bytes."""
    sizes: dict[str, int] = {}
    for root, _, files in os.walk(local_root):
        for name in files:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, local_root)
            try:
                sizes[rel.replace(os.sep, "/")] = os.path.getsize(abs_path)
            except OSError:
                continue
    return sizes


def _list_remote_sizes(client, bucket: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            sizes[obj["Key"]] = obj["Size"]
    return sizes


def _upload_all(
    client,
    bucket_name: str,
    local_root: str,
    keys: list[str],
    workers: int = _UPLOAD_WORKERS,
) -> tuple[list[str], list[str]]:
    """Upload ``keys`` concurrently; returns ``(uploaded, failed)``, both sorted."""
    uploaded: list[str] = []
    failed: list[str] = []

    def upload(key: str) -> None:
        local_path = os.path.join(local_root, key.replace("/", os.sep))
        client.upload_file(local_path, bucket_name, key, ExtraArgs=_upload_args(key))

    if workers <= 1 or len(keys) <= 1:
        for key in keys:
            try:
                upload(key)
            except Exception as e:
                logger.error(f"===> failed to upload {key}: {e}")
                failed.append(key)
            else:
                uploaded.append(key)
                logger.info(f"===> uploaded {key}")
        return sorted(uploaded), sorted(failed)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(upload, key): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"===> failed to upload {key}: {e}")
                failed.append(key)
            else:
                uploaded.append(key)
                logger.info(f"===> uploaded {key}")
    return sorted(uploaded), sorted(failed)


def _top_dir(key: str) -> str:
    return key.split("/", 1)[0] if "/" in key else ""


def _needs_upload(
    key: str,
    local_size: int,
    remote_sizes: dict[str, int],
    force_prefixes: tuple[str, ...],
    force_keys: tuple[str, ...],
) -> bool:
    if key in force_keys or _top_dir(key) in force_prefixes:
        return True
    if key not in remote_sizes:
        return True
    # Size is the only cheap signal available from list_objects_v2; identical
    # size means "very likely unchanged". Small volatile files should be listed
    # in force_keys by the caller instead.
    return remote_sizes[key] != local_size


def sync(
    local_root: str,
    bucket_name: str,
    client,
    force_prefixes: tuple[str, ...] = (),
    force_keys: tuple[str, ...] = (),
) -> SyncResult:
    """Mirror local_root to bucket.

    ``force_prefixes`` re-uploads every key under those top-level directories;
    ``force_keys`` re-uploads exact keys (e.g. ``index.html``, whose content
    changes every run but whose size may not). Both exist because
    list_objects_v2 only exposes size, not content hashes.
    """
    local_sizes = _walk_local_keys(local_root)
    remote_sizes = _list_remote_sizes(client, bucket_name)

    to_upload = sorted(
        key
        for key, size in local_sizes.items()
        if _needs_upload(key, size, remote_sizes, force_prefixes, force_keys)
    )
    to_delete = sorted(set(remote_sizes) - set(local_sizes))
    to_skip = sorted(set(local_sizes) - set(to_upload))

    uploaded, failed = _upload_all(client, bucket_name, local_root, to_upload)
    result = SyncResult(uploaded=uploaded, deleted=[], skipped=to_skip, failed=failed)

    for key in to_delete:
        try:
            client.delete_object(Bucket=bucket_name, Key=key)
        except ClientError as e:
            logger.error(f"===> failed to delete {key}: {e}")
            result.failed.append(key)
            continue
        result.deleted.append(key)
        logger.info(f"===> deleted {key}")

    return result
