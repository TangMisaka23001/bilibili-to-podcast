"""prune 必须让「合集目录」与「它的 feed」保持一致。

合集被 prune 掉之后，``rss/{kind}/{sid}.xml`` 里的每个 episode 都会指向
404，所以这份 feed 必须跟着一起删，否则桶里会永久积累僵尸订阅源。
"""
from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from bilibili_podcast import store as store_mod
from bilibili_podcast.cli import prune as prune_mod
from bilibili_podcast.store import S3Path

BUCKET = "prune-feed-bucket"


def _write_sources(tmp_path: Path, all_urls: list[str], new_urls: list[str]) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "RSS_URL_PREFIX: https://podcast.example.com/\n"
        "storage:\n"
        "  backend: s3\n"
        "R2:\n"
        "  ACCESS_KEY: key\n"
        "  SECRET_KEY: secret\n"
        "  ENDPOINT_URL: https://example.invalid\n"
        "  BUCKET_NAME: " + BUCKET + "\n",
        encoding="utf-8",
    )
    (tmp_path / "sources.json").write_text(
        json.dumps({"all": all_urls, "new": new_urls}), encoding="utf-8"
    )
    return cfg


@pytest.fixture
def aws_bucket(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(store_mod, "make_s3_client", lambda *a, **k: client)
        yield client


def _keys(client) -> set[str]:
    resp = client.list_objects_v2(Bucket=BUCKET)
    return {obj["Key"] for obj in resp.get("Contents", [])}


def test_local_prune_removes_feed_of_pruned_collection(tmp_path):
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        [],
    )
    out = tmp_path / "output"
    (out / "bilibili-season" / "999" / "BV1aa").mkdir(parents=True)
    (out / "bilibili-season" / "999" / "BV1aa" / "BV1aa.m4a").write_bytes(b"x")
    (out / "rss" / "season").mkdir(parents=True)
    (out / "rss" / "season" / "999.xml").write_text("<rss/>")
    (out / "rss" / "season" / "598034.xml").write_text("<rss/>")

    result = prune_mod.prune(str(cfg), str(out), backend="local")
    assert result.deleted_season == ["999"]
    assert not (out / "bilibili-season" / "999").exists()
    # 被 prune 的合集，其 feed 也必须消失。
    assert not (out / "rss" / "season" / "999.xml").exists()
    # 仍在配置里的合集，feed 必须保留。
    assert (out / "rss" / "season" / "598034.xml").exists()


def test_remote_prune_removes_feed_of_pruned_collection(tmp_path, aws_bucket):
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        [],
    )
    from bilibili_podcast.config_loader import load_active_config

    loaded = load_active_config(str(cfg))
    root = store_mod.resolve_root(loaded, "output", "s3")
    assert isinstance(root, S3Path)
    store = root.store
    store.put_bytes("bilibili-season/999/BV1aa/BV1aa.m4a", b"x")
    store.put_bytes("bilibili-season/598034/BV1aa/BV1aa.m4a", b"x")
    store.put_bytes("rss/season/999.xml", b"<rss/>")
    store.put_bytes("rss/season/598034.xml", b"<rss/>")

    result = prune_mod.prune(str(cfg), "output", backend="s3")
    assert result.deleted_season == ["999"]
    keys = _keys(aws_bucket)
    assert "bilibili-season/999/BV1aa/BV1aa.m4a" not in keys
    assert "rss/season/999.xml" not in keys
    assert "bilibili-season/598034/BV1aa/BV1aa.m4a" in keys
    assert "rss/season/598034.xml" in keys


def test_prune_keeps_new_feed(tmp_path):
    """new 合集共用 rss/new.xml，剪掉某个 new sid 不能删掉整份 feed。"""
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        ["https://space.bilibili.com/2/lists/100?type=season"],
    )
    out = tmp_path / "output"
    (out / "bilibili-new" / "200").mkdir(parents=True)
    (out / "rss").mkdir(parents=True)
    (out / "rss" / "new.xml").write_text("<rss/>")

    result = prune_mod.prune(str(cfg), str(out), backend="local")
    assert result.deleted_new == ["200"]
    assert (out / "rss" / "new.xml").exists()


def test_dry_run_reports_feeds(tmp_path, capsys):
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        [],
    )
    out = tmp_path / "output"
    (out / "bilibili-season" / "999").mkdir(parents=True)
    (out / "rss" / "season").mkdir(parents=True)
    (out / "rss" / "season" / "999.xml").write_text("<rss/>")

    rc = prune_mod.main(
        ["--config", str(cfg), "--output-root", str(out), "--backend", "local", "--dry-run"]
    )
    out_text = capsys.readouterr().out
    assert rc == 0
    assert "would delete (season): ['999']" in out_text
    assert "would delete (season feeds)" in out_text
    # dry-run 不真的删。
    assert (out / "bilibili-season" / "999").exists()
    assert (out / "rss" / "season" / "999.xml").exists()


def test_orphan_feed_is_pruned_even_without_its_directory(tmp_path):
    """合集目录早没了、feed 还留着 —— 这份 feed 里的 episode 全是 404，必须清掉。"""
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        [],
    )
    out = tmp_path / "output"
    (out / "rss" / "season").mkdir(parents=True)
    (out / "rss" / "season" / "777.xml").write_text("<rss/>")
    (out / "rss" / "season" / "598034.xml").write_text("<rss/>")

    rc = prune_mod.main(
        ["--config", str(cfg), "--output-root", str(out), "--backend", "local"]
    )
    assert rc == 0
    assert not (out / "rss" / "season" / "777.xml").exists()
    assert (out / "rss" / "season" / "598034.xml").exists()


def test_remote_orphan_feed_is_pruned(tmp_path, aws_bucket):
    cfg = _write_sources(
        tmp_path,
        ["https://space.bilibili.com/1/lists/598034?type=season"],
        [],
    )
    from bilibili_podcast.config_loader import load_active_config

    loaded = load_active_config(str(cfg))
    root = store_mod.resolve_root(loaded, "output", "s3")
    store = root.store
    store.put_bytes("bilibili-season/598034/BV1aa/BV1aa.m4a", b"x")
    store.put_bytes("rss/season/777.xml", b"<rss/>")
    store.put_bytes("rss/season/598034.xml", b"<rss/>")

    rc = prune_mod.main(
        ["--config", str(cfg), "--output-root", "output", "--backend", "s3"]
    )
    assert rc == 0
    keys = _keys(aws_bucket)
    assert "rss/season/777.xml" not in keys
    assert "rss/season/598034.xml" in keys
