"""S3 (Cloudflare R2) sync: mirror a local root directory into a bucket.

After sync() returns, the bucket's keys are exactly the local root's relative
file paths. Keys are uploaded when local-only, when their size changed, or when
forced (see ``force_prefixes`` / ``force_keys``); remote-only keys are deleted.
"""
from __future__ import annotations

import hashlib
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

#: 小于该体积的文件在「大小相同」时再比对内容哈希。S3 对单段上传的 ETag
#: 就是文件 md5，所以能和本地 md5 直接比。小文件（RSS/JSON/HTML）常出现
#: 「改一个字符、字节数不变」的情况，只比 Size 会漏掉更新。
_HASH_SIZE_LIMIT = 1 << 20  # 1 MiB


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


#: Podcasting 2.0 要求章节文件用这个 type（不是普通的 application/json）。
CHAPTERS_KEY_SUFFIX = "/chapters.json"
CHAPTERS_CONTENT_TYPE = "application/json+chapters"


def _content_type(key: str) -> str:
    if key.endswith(CHAPTERS_KEY_SUFFIX):
        return CHAPTERS_CONTENT_TYPE
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


def _list_remote(client, bucket: str) -> dict[str, tuple[int, str]]:
    """key -> (size, etag)。ETag 单段上传时即 md5。"""
    remote: dict[str, tuple[int, str]] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            remote[obj["Key"]] = (obj["Size"], obj.get("ETag", "").strip('"'))
    return remote


def _md5(path: str) -> str:
    """本地文件 md5 —— 只用于和 S3 单段上传的 ETag 比对，不是安全用途。"""
    # S3 单段上传的 ETag 定义为 md5，必须用同一算法才能比对；
    # 这里只做变更检测，不涉及任何安全用途。
    digest = hashlib.new("md5", usedforsecurity=False)
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


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
    local_path: str,
    remote: dict[str, tuple[int, str]],
    force_prefixes: tuple[str, ...],
    force_keys: tuple[str, ...],
    force_suffixes: tuple[str, ...] = (),
) -> bool:
    if key in force_keys or _top_dir(key) in force_prefixes:
        return True
    if force_suffixes and key.endswith(force_suffixes):
        return True
    if key not in remote:
        return True

    remote_size, remote_etag = remote[key]
    local_size = os.path.getsize(local_path)
    if remote_size != local_size:
        return True

    # 大小相同不等于内容相同：小文件用 md5 和 ETag 比对（多段上传的 ETag 带
    # "-<part>" 后缀，此时无法直接比 md5，退回到「大小相同视为未变」）。
    if local_size <= _HASH_SIZE_LIMIT and remote_etag and "-" not in remote_etag:
        return _md5(local_path) != remote_etag
    return False


def sync(
    local_root: str,
    bucket_name: str,
    client,
    force_prefixes: tuple[str, ...] = (),
    force_keys: tuple[str, ...] = (),
    force_suffixes: tuple[str, ...] = (),
) -> SyncResult:
    """Mirror local_root to bucket.

    ``force_prefixes`` re-uploads every key under those top-level directories;
    ``force_keys`` re-uploads exact keys (e.g. ``index.html``, whose content
    changes every run but whose size may not); ``force_suffixes`` re-uploads
    keys by suffix (e.g. ``chapters.json``, so a Content-Type fix reaches
    objects whose bytes did not change).
    """
    local_sizes = _walk_local_keys(local_root)
    remote = _list_remote(client, bucket_name)

    to_upload = sorted(
        key
        for key in local_sizes
        if _needs_upload(
            key,
            os.path.join(local_root, key.replace("/", os.sep)),
            remote,
            force_prefixes,
            force_keys,
            force_suffixes,
        )
    )
    to_delete = sorted(set(remote) - set(local_sizes))
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
