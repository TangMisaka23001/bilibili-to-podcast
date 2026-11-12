"""Tests for bilibili_podcast.storage: content-aware uploads and metadata."""
from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from bilibili_podcast.storage import _cache_control, _content_type, make_s3_client, sync


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="bucket")
        yield client


def _write(root: Path, rel: str, body: bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_make_s3_client_returns_client():
    assert make_s3_client("ak", "sk", "https://example.com") is not None


def test_content_type_follows_extension():
    assert _content_type("index.html") == "text/html"
    assert _content_type("rss/new.xml") == "application/xml"
    assert _content_type("a/BV1.m4a") == "audio/mp4"
    assert _content_type("a/pic.jpg") == "image/jpeg"
    assert _content_type("a/complete") == "application/octet-stream"


def test_cache_control_is_immutable_for_media():
    assert "immutable" in _cache_control("a/BV1.m4a")
    assert "immutable" in _cache_control("a/pic.jpg")
    assert _cache_control("index.html") == "max-age=300"


def test_sync_uploads_when_size_changed(s3, tmp_path: Path):
    _write(tmp_path, "a/BV1.m4a", b"new-bytes")
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"old")

    result = sync(str(tmp_path), "bucket", s3)

    assert result.uploaded == ["a/BV1.m4a"]
    assert result.skipped == []


def test_sync_skips_when_size_matches(s3, tmp_path: Path):
    _write(tmp_path, "a/BV1.m4a", b"same")
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"same")

    result = sync(str(tmp_path), "bucket", s3)

    assert result.uploaded == []
    assert result.skipped == ["a/BV1.m4a"]


def test_sync_force_keys_uploads_same_size(s3, tmp_path: Path):
    _write(tmp_path, "index.html", b"v2")
    s3.put_object(Bucket="bucket", Key="index.html", Body=b"v1")

    result = sync(str(tmp_path), "bucket", s3, force_keys=("index.html",))

    assert result.uploaded == ["index.html"]


def test_sync_sets_content_type_and_cache_control(s3, tmp_path: Path):
    _write(tmp_path, "index.html", b"<html/>")
    _write(tmp_path, "a/BV1.m4a", b"audio")

    sync(str(tmp_path), "bucket", s3)

    html = s3.head_object(Bucket="bucket", Key="index.html")
    assert html["ContentType"] == "text/html"
    assert html["CacheControl"] == "max-age=300"

    audio = s3.head_object(Bucket="bucket", Key="a/BV1.m4a")
    assert audio["ContentType"] == "audio/mp4"
    assert "immutable" in audio["CacheControl"]


def test_sync_records_failed_uploads(s3, tmp_path: Path, monkeypatch):
    _write(tmp_path, "a.m4a", b"audio")

    def boom(*args, **kwargs):
        raise RuntimeError("upload exploded")

    monkeypatch.setattr(s3, "upload_file", boom)

    result = sync(str(tmp_path), "bucket", s3)

    assert result.failed == ["a.m4a"]
    assert result.uploaded == []
