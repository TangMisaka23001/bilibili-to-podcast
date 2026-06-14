from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from src.upload_r2 import sync


@pytest.fixture
def aws_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client


def _seed_files(root: Path) -> None:
    (root / "bilibili-season" / "598034").mkdir(parents=True)
    (root / "bilibili-season" / "598034" / "BV1.mp4.m4a").write_text("audio-bytes")
    (root / "bilibili-season" / "598034" / "videos.json").write_text("[]")
    (root / "rss").mkdir(parents=True, exist_ok=True)
    (root / "rss" / "598034.xml").write_text("<rss/>")


def _keys_in_bucket(client) -> set[str]:
    keys = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket="test-bucket"):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def test_sync_empty_local_and_empty_bucket_uploads_nothing(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)
    for p in local_root.rglob("*"):
        if p.is_file():
            p.unlink()

    result = sync(str(local_root), "test-bucket", aws_client)

    assert result.uploaded == []
    assert result.deleted == []
    assert _keys_in_bucket(aws_client) == set()


def test_sync_local_only_uploads_everything(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)

    result = sync(str(local_root), "test-bucket", aws_client)

    assert set(result.uploaded) == {
        "bilibili-season/598034/BV1.mp4.m4a",
        "bilibili-season/598034/videos.json",
        "rss/598034.xml",
    }
    assert result.deleted == []
    assert _keys_in_bucket(aws_client) == set(result.uploaded)


def test_sync_remote_only_deletes_everything(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)
    for p in local_root.rglob("*"):
        if p.is_file():
            p.unlink()

    aws_client.put_object(Bucket="test-bucket", Key="bilibili-season/999/BV2.m4a", Body=b"x")
    aws_client.put_object(Bucket="test-bucket", Key="rss/999.xml", Body=b"<rss/>")

    result = sync(str(local_root), "test-bucket", aws_client)

    assert result.uploaded == []
    assert set(result.deleted) == {"bilibili-season/999/BV2.m4a", "rss/999.xml"}
    assert _keys_in_bucket(aws_client) == set()


def test_sync_identical_state_is_noop(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)
    for p in local_root.rglob("*"):
        if p.is_file():
            aws_client.put_object(
                Bucket="test-bucket",
                Key=str(p.relative_to(local_root)),
                Body=p.read_bytes(),
            )

    result = sync(str(local_root), "test-bucket", aws_client)

    assert result.uploaded == []
    assert result.deleted == []
    assert len(result.skipped) == 3


def test_sync_drift_uploads_new_and_deletes_old(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)

    aws_client.put_object(Bucket="test-bucket", Key="bilibili-season/598034/OLD.m4a", Body=b"x")
    (local_root / "bilibili-season" / "598034" / "NEW.m4a").write_text("new")

    result = sync(str(local_root), "test-bucket", aws_client)

    assert "bilibili-season/598034/NEW.m4a" in result.uploaded
    assert "bilibili-season/598034/OLD.m4a" in result.deleted
    keys = _keys_in_bucket(aws_client)
    assert "bilibili-season/598034/OLD.m4a" not in keys
    assert "bilibili-season/598034/NEW.m4a" in keys


def test_sync_videos_json_is_not_special(aws_client, tmp_path):
    local_root = tmp_path / "output"
    local_root.mkdir()
    _seed_files(local_root)
    aws_client.put_object(
        Bucket="test-bucket",
        Key="bilibili-season/598034/videos.json",
        Body=b"[]",
    )

    result = sync(str(local_root), "test-bucket", aws_client)

    assert "bilibili-season/598034/videos.json" not in result.uploaded
    assert "bilibili-season/598034/videos.json" in result.skipped
