"""S3 (Cloudflare R2) sync: mirror a local root directory into a bucket.

After sync() returns, the bucket's keys are exactly the local root's relative
file paths. Keys are uploaded when local-only, when their size changed, or when
forced (see ``force_prefixes`` / ``force_keys``); remote-only keys are deleted.

pull() is the inverse direction: it materialises the bucket's keys under a
local root instead of re-downloading the source material. It never deletes
locally-only files, so it composes with the fetch pipeline instead of
replacing it.
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

#: Intermediate download artefacts that must never be published. yt-dlp leaves
#: ``.part``/``.ytdl`` behind when a download dies mid-flight, and the
#: playurl fallback downloads into a ``.m4s`` segment before remuxing it.
#: Uploading those would put junk in the bucket — a truncated ``.m4s`` next
#: to the real ``.m4a`` — and every stale object is extra bytes to serve.
_INTERMEDIATE_SUFFIXES = (".part", ".ytdl", ".m4s")

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


@dataclass
class PullResult:
    """Outcome of a pull: what was downloaded, skipped, and what failed."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0


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


def _is_intermediate(key: str) -> bool:
    """Whether a key is a download artefact rather than a publishable file."""
    return key.endswith(_INTERMEDIATE_SUFFIXES)


def _walk_local_keys(local_root: str) -> dict[str, int]:
    """Map every local file's relative key to its size in bytes.

    Download artefacts are left out: they are not part of the published
    output, so they must not be uploaded — and they must not make the
    bucket keep a stale copy either (see :func:`sync`'s mirror deletion).
    """
    sizes: dict[str, int] = {}
    for root, _, files in os.walk(local_root):
        for name in files:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, local_root)
            key = rel.replace(os.sep, "/")
            if _is_intermediate(key):
                logger.info(f"===> skipping intermediate file {key}")
                continue
            try:
                sizes[key] = os.path.getsize(abs_path)
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


def _content_matches(local_path: str, remote_size: int, remote_etag: str) -> bool:
    """True when the local file is identical to the remote object.

    Size is the cheap first check. Small files are then compared by md5
    against the S3 ETag, because a one-character edit (a chapters.json first
    chapter going 1 -> 0) keeps the byte count the same. Objects uploaded in
    parts carry an ETag with a ``-<part>`` suffix and cannot be compared that
    way, so those fall back to "same size means unchanged".
    """
    try:
        local_size = os.path.getsize(local_path)
    except OSError:
        return False
    if local_size != remote_size:
        return False
    if local_size <= _HASH_SIZE_LIMIT and remote_etag and "-" not in remote_etag:
        return _md5(local_path) == remote_etag
    return True


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
    return not _content_matches(local_path, remote_size, remote_etag)


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


#: Downloads are network-bound; a small pool keeps a full restore from
#: serialising hundreds of round trips.
_DOWNLOAD_WORKERS = 8


def _local_path(local_root: str, key: str) -> str:
    return os.path.join(local_root, key.replace("/", os.sep))


def _download_one(
    client,
    bucket_name: str,
    local_root: str,
    key: str,
    remote_size: int,
) -> int:
    """Download a single key; returns the number of bytes written.

    The body is streamed into a ``.part`` file and moved into place only after
    it is complete, so an interrupted run never leaves a truncated file that a
    later size check would mistake for a complete one.
    """
    local_path = _local_path(local_root, key)
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    part_path = local_path + ".part"

    written = 0
    response = client.get_object(Bucket=bucket_name, Key=key)
    body = response["Body"]
    try:
        with open(part_path, "wb") as handle:
            for chunk in body.iter_chunks(chunk_size=_HASH_SIZE_LIMIT):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
    finally:
        body.close()

    if remote_size and written != remote_size:
        raise RuntimeError(f"incomplete download: {written}/{remote_size} bytes")

    os.replace(part_path, local_path)
    return written


def pull(
    local_root: str,
    bucket_name: str,
    client,
    prefixes: tuple[str, ...] = (),
    workers: int = _DOWNLOAD_WORKERS,
) -> PullResult:
    """Download the bucket's objects under ``local_root``.

    ``prefixes`` restricts the download to keys starting with one of those
    prefixes (e.g. ``("bilibili-season/", "rss/")``); an empty tuple pulls
    everything. Keys whose local file already exists with the same size are
    skipped, which makes the call safe to repeat after an interrupted run.

    Unlike :func:`sync` this never deletes local files that the bucket does
    not have: the bucket is a cache of the published output, not the source of
    truth for a fresh local tree.
    """
    remote = _list_remote(client, bucket_name)
    keys = sorted(
        key
        for key, (_size, _etag) in remote.items()
        if not prefixes or key.startswith(prefixes)
    )
    keys = [key for key in keys if not _is_intermediate(key)]

    to_download: list[tuple[str, int]] = []
    to_skip: list[str] = []
    for key in keys:
        size, etag = remote[key]
        if _content_matches(_local_path(local_root, key), size, etag):
            to_skip.append(key)
        else:
            to_download.append((key, size))

    downloaded: list[str] = []
    failed: list[str] = []
    total_bytes = 0

    def fetch(item: tuple[str, int]) -> tuple[str, int]:
        key, size = item
        return key, _download_one(client, bucket_name, local_root, key, size)

    if workers <= 1 or len(to_download) <= 1:
        for item in to_download:
            try:
                key, written = fetch(item)
            except Exception as e:
                logger.error(f"===> failed to download {item[0]}: {e}")
                failed.append(item[0])
            else:
                downloaded.append(key)
                total_bytes += written
                logger.info(f"===> downloaded {key}")
        return PullResult(
            downloaded=sorted(downloaded),
            skipped=sorted(to_skip),
            failed=sorted(failed),
            bytes_downloaded=total_bytes,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, item): item for item in to_download}
        for future in as_completed(futures):
            key = futures[future][0]
            try:
                _, written = future.result()
            except Exception as e:
                logger.error(f"===> failed to download {key}: {e}")
                failed.append(key)
            else:
                downloaded.append(key)
                total_bytes += written
                logger.info(f"===> downloaded {key}")

    return PullResult(
        downloaded=sorted(downloaded),
        skipped=sorted(to_skip),
        failed=sorted(failed),
        bytes_downloaded=total_bytes,
    )
