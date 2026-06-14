"""S3 (Cloudflare R2) sync: mirror a local root directory exactly into a bucket.

After sync() returns, the bucket's keys are exactly the local root's relative file
paths. Local-only keys are uploaded; remote-only keys are deleted; identical keys
are skipped.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import boto3
from botocore.exceptions import ClientError

from bilibili_podcast.logger import get_logger

logger = get_logger()


@dataclass
class SyncResult:
    uploaded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def make_s3_client(
    access_key: str,
    secret_key: str,
    endpoint_url: str,
):
    return boto3.session.Session().client(
        service_name="s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint_url,
    )


def _walk_local_keys(local_root: str) -> set[str]:
    keys: set[str] = set()
    for root, _, files in os.walk(local_root):
        for name in files:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, local_root)
            keys.add(rel.replace(os.sep, "/"))
    return keys


def _list_remote_keys(client, bucket: str) -> set[str]:
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def sync(
    local_root: str,
    bucket_name: str,
    client,
) -> SyncResult:
    local_keys = _walk_local_keys(local_root)
    remote_keys = _list_remote_keys(client, bucket_name)

    to_upload = sorted(local_keys - remote_keys)
    to_delete = sorted(remote_keys - local_keys)
    to_skip = sorted(local_keys & remote_keys)

    result = SyncResult(uploaded=to_upload, deleted=to_delete, skipped=to_skip)

    for key in to_upload:
        local_path = os.path.join(local_root, key.replace("/", os.sep))
        try:
            client.upload_file(local_path, bucket_name, key)
            logger.info(f"===> uploaded {key}")
        except Exception as e:
            logger.error(f"===> failed to upload {key}: {e}")

    for key in to_delete:
        try:
            client.delete_object(Bucket=bucket_name, Key=key)
            logger.info(f"===> deleted {key}")
        except ClientError as e:
            logger.error(f"===> failed to delete {key}: {e}")

    return result


# --- Backwards-compatible helpers used by file.py -----------------------------
# These keep the original 2-arg signature (object_key, bucket_name) so the
# existing file.py imports continue to work; they construct a client lazily.

_LAZY_CLIENT = None


def _default_client():
    global _LAZY_CLIENT
    if _LAZY_CLIENT is None:
        from bilibili_podcast.config import ACCESS_KEY, ENDPOINT_URL, SECRET_KEY
        _LAZY_CLIENT = make_s3_client(ACCESS_KEY, SECRET_KEY, ENDPOINT_URL)
    return _LAZY_CLIENT


def object_exists(object_key: str, bucket_name: str) -> bool:
    client = _default_client()
    try:
        client.head_object(Bucket=bucket_name, Key=object_key)
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        raise
    return True


def get_object(object_key: str, bucket_name: str):
    client = _default_client()
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    return json.loads(response["Body"].read().decode("utf-8"))


if __name__ == "__main__":
    from bilibili_podcast.config import BUCKET_NAME
    client = _default_client()
    result = sync("../output/", BUCKET_NAME, client)
    logger.info(
        f"===> sync done: uploaded={len(result.uploaded)} "
        f"deleted={len(result.deleted)} skipped={len(result.skipped)}"
    )
