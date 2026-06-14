"""Tests for sync() force_prefixes behavior."""
from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from bilibili_podcast.storage import _top_dir, sync


@pytest.fixture
def aws_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client


def _write(root: Path, rel: str, body: bytes = b"x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)


def test_top_dir_extracts_first_segment():
    assert _top_dir("rss/598034.xml") == "rss"
    assert _top_dir("bilibili-season/598034/BV1.m4a") == "bilibili-season"
    assert _top_dir("lone-file.txt") == ""


def test_sync_without_force_skips_existing(aws_client, tmp_path):
    _write(tmp_path, "rss/598034.xml")
    aws_client.put_object(Bucket="test-bucket", Key="rss/598034.xml", Body=b"x")

    result = sync(str(tmp_path), "test-bucket", aws_client)

    assert result.uploaded == []
    assert "rss/598034.xml" in result.skipped


def test_sync_force_prefix_uploads_even_when_exists(aws_client, tmp_path):
    _write(tmp_path, "rss/598034.xml", body=b"new")
    aws_client.put_object(Bucket="test-bucket", Key="rss/598034.xml", Body=b"old")

    result = sync(str(tmp_path), "test-bucket", aws_client, force_prefixes=("rss",))

    assert result.uploaded == ["rss/598034.xml"]
    assert result.skipped == []


def test_sync_force_prefix_does_not_affect_other_dirs(aws_client, tmp_path):
    _write(tmp_path, "rss/598034.xml")
    _write(tmp_path, "bilibili-season/10/BV1.m4a")
    aws_client.put_object(Bucket="test-bucket", Key="rss/598034.xml", Body=b"x")
    aws_client.put_object(Bucket="test-bucket", Key="bilibili-season/10/BV1.m4a", Body=b"x")

    result = sync(str(tmp_path), "test-bucket", aws_client, force_prefixes=("rss",))

    assert "rss/598034.xml" in result.uploaded
    assert "bilibili-season/10/BV1.m4a" in result.skipped


def test_sync_multiple_force_prefixes(aws_client, tmp_path):
    _write(tmp_path, "rss/598034.xml")
    _write(tmp_path, "metadata/x.json")
    aws_client.put_object(Bucket="test-bucket", Key="rss/598034.xml", Body=b"x")
    aws_client.put_object(Bucket="test-bucket", Key="metadata/x.json", Body=b"x")

    result = sync(str(tmp_path), "test-bucket", aws_client, force_prefixes=("rss", "metadata"))

    assert sorted(result.uploaded) == ["metadata/x.json", "rss/598034.xml"]
    assert result.skipped == []
