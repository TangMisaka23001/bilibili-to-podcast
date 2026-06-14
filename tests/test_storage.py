"""Tests for bilibili_podcast.storage helpers: object_exists, get_object, make_s3_client."""
from __future__ import annotations

import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from bilibili_podcast.storage import get_object, make_s3_client, object_exists


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="bucket")
        client.put_object(Bucket="bucket", Key="hello.json", Body=b'{"msg":"world"}')
        yield client


def test_make_s3_client_returns_client():
    c = make_s3_client("ak", "sk", "https://example.com")
    assert c is not None


def test_object_exists_returns_true_for_existing(s3, monkeypatch):
    monkeypatch.setattr("bilibili_podcast.storage._default_client", lambda: s3)
    assert object_exists("hello.json", "bucket") is True


def test_object_exists_returns_false_for_missing(s3, monkeypatch):
    monkeypatch.setattr("bilibili_podcast.storage._default_client", lambda: s3)
    assert object_exists("missing.json", "bucket") is False


def test_object_exists_re_raises_non_404_errors(s3, monkeypatch):
    monkeypatch.setattr("bilibili_podcast.storage._default_client", lambda: s3)
    with pytest.raises(ClientError):
        object_exists("hello.json", "no-such-bucket")


def test_get_object_reads_json(s3, monkeypatch):
    monkeypatch.setattr("bilibili_podcast.storage._default_client", lambda: s3)
    assert get_object("hello.json", "bucket") == {"msg": "world"}
