"""Tests for bilibili_podcast.storage.pull: bucket -> local restore."""
from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from bilibili_podcast.storage import make_s3_client, pull


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="bucket")
        yield client


def test_pull_downloads_all_keys(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")
    s3.put_object(Bucket="bucket", Key="index.html", Body=b"<html/>")

    result = pull(str(tmp_path), "bucket", s3)

    assert result.downloaded == ["a/BV1.m4a", "index.html"]
    assert result.failed == []
    assert (tmp_path / "a" / "BV1.m4a").read_bytes() == b"audio"
    assert (tmp_path / "index.html").read_bytes() == b"<html/>"


def test_pull_skips_matching_size_files(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "BV1.m4a").write_bytes(b"audio")

    result = pull(str(tmp_path), "bucket", s3)

    assert result.downloaded == []
    assert result.skipped == ["a/BV1.m4a"]


def test_pull_redownloads_when_size_differs(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio-new")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "BV1.m4a").write_bytes(b"truncated")

    result = pull(str(tmp_path), "bucket", s3)

    assert result.downloaded == ["a/BV1.m4a"]
    assert (tmp_path / "a" / "BV1.m4a").read_bytes() == b"audio-new"


def test_pull_respects_prefixes(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="rss/season/1.xml", Body=b"<rss/>")
    s3.put_object(Bucket="bucket", Key="bilibili-season/1/BV1/complete", Body=b"")

    result = pull(str(tmp_path), "bucket", s3, prefixes=("rss/",))

    assert result.downloaded == ["rss/season/1.xml"]
    assert not (tmp_path / "bilibili-season").exists()


def test_pull_never_deletes_local_only_files(s3, tmp_path: Path):
    """Pull is additive: a local file the bucket lacks must survive."""
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")
    (tmp_path / "local-only.txt").write_text("keep me")

    pull(str(tmp_path), "bucket", s3)

    assert (tmp_path / "local-only.txt").read_text() == "keep me"


def test_pull_leaves_no_part_files(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")

    pull(str(tmp_path), "bucket", s3)

    assert list(tmp_path.rglob("*.part")) == []


def test_pull_reports_failed_downloads(s3, tmp_path: Path, monkeypatch):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")

    def boom(*args, **kwargs):
        raise RuntimeError("download exploded")

    monkeypatch.setattr(
        "bilibili_podcast.storage._download_one", boom
    )

    result = pull(str(tmp_path), "bucket", s3)

    assert result.failed == ["a/BV1.m4a"]
    assert result.downloaded == []


def test_pull_is_idempotent(s3, tmp_path: Path):
    s3.put_object(Bucket="bucket", Key="a/BV1.m4a", Body=b"audio")

    first = pull(str(tmp_path), "bucket", s3)
    second = pull(str(tmp_path), "bucket", s3)

    assert first.downloaded == ["a/BV1.m4a"]
    assert second.downloaded == []
    assert second.skipped == ["a/BV1.m4a"]


def test_pull_downloads_in_parallel(s3, tmp_path: Path):
    for i in range(5):
        s3.put_object(Bucket="bucket", Key=f"a/BV{i}.m4a", Body=b"audio")

    result = pull(str(tmp_path), "bucket", s3, workers=4)

    assert len(result.downloaded) == 5
    assert result.failed == []


def test_pull_restores_complete_markers(s3, tmp_path: Path):
    """The `complete` marker is what makes the fetch pipeline skip a BV."""
    s3.put_object(Bucket="bucket", Key="bilibili-season/1/BV1/complete", Body=b"")

    pull(str(tmp_path), "bucket", s3)

    assert (tmp_path / "bilibili-season" / "1" / "BV1" / "complete").exists()


def test_make_s3_client_with_r2_endpoint():
    client = make_s3_client("ak", "sk", "https://example.r2.cloudflarestorage.com")
    assert client is not None
