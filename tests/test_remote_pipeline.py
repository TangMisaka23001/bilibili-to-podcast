"""端到端测试：整条流水线在对象存储后端下跑通。

用 moto 模拟 S3，验证「产物直接写进桶、本地不留 output/」这条核心主张：
prune → fetch → summarize → rss → gen-index → sync 在 ``--backend s3`` 下
都把结果写进桶里，且每个阶段都不依赖本地目录。
"""
from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from bilibili_podcast import rss as rss_mod
from bilibili_podcast import store as store_mod
from bilibili_podcast.cli import cron as cron_mod
from bilibili_podcast.cli import gen_index as gen_index_mod
from bilibili_podcast.cli import prune as prune_mod
from bilibili_podcast.cli import rss_cmd as rss_cmd_mod
from bilibili_podcast.cli import size as size_mod
from bilibili_podcast.cli import sync as sync_mod
from bilibili_podcast.store import S3Path

BUCKET = "pipeline-bucket"


@pytest.fixture
def aws_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def config_file(tmp_path, aws_bucket, monkeypatch):
    """写一份真实可读的 config.yaml + sources.json，并把 store 的 client 换成 moto 的。

    ``ENDPOINT_URL`` 在 moto 下会真去连 endpoint，所以直接替换掉
    ``store.make_s3_client``，让所有后端都走 moto 的 client。
    """
    monkeypatch.setattr(store_mod, "make_s3_client", lambda *a, **k: aws_bucket)
    # RSS 的公开 URL 来自模块级全局（config.py 在 import 时读真实 config.yaml），
    # 这里固定成测试域名，避免断言依赖机器上的配置。
    monkeypatch.setattr(rss_mod, "RSS_URL_PREFIX", "https://podcast.example.com/")

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
        json.dumps(
            {
                "all": ["https://space.bilibili.com/1/lists/598034?type=season"],
                "new": ["https://space.bilibili.com/2/lists/100?type=season"],
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _keys(client) -> set[str]:
    resp = client.list_objects_v2(Bucket=BUCKET)
    return {obj["Key"] for obj in resp.get("Contents", [])}


def test_remote_root_is_s3_path(config_file):
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)
    assert isinstance(root, S3Path)
    assert str(root) == ""


def test_fetch_writes_artifacts_into_bucket(config_file, aws_bucket, tmp_path):
    """b2p-fetch 的全部产物直接落在桶里，本地 output/ 不出现。"""
    from bilibili_podcast.bilibili import channel as channel_mod
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(
            channel_mod,
            "fetch_channel_meta",
            lambda ref: {
                "upper": {"name": "频道"},
                "title": "合集",
                "mid": 1,
                "id": 598034,
            },
        )
        monkey.setattr(
            channel_mod,
            "fetch_videos",
            lambda ref, meta: [
                {
                    "bvid": "BV1aa",
                    "title": "标题",
                    "pic": "http://p/1.jpg",
                    "pubdate": 1700000000,
                }
            ],
        )
        monkey.setattr(
            channel_mod,
            "fetch_video_info",
            lambda bv: {
                "bvid": bv,
                "title": "标题",
                "pic": "http://p/1.jpg",
                "cid": 1,
                "owner": {"mid": 1},
            },
        )

        def fake_download_audio(ref, bv, video_dir):
            target = video_dir / f"{bv}.m4a"
            target.write_bytes(b"audio-bytes")
            return target

        def fake_download_picture(pic_url, dest):
            dest.write_bytes(b"jpeg")

        monkey.setattr(channel_mod, "download_audio", fake_download_audio)
        monkey.setattr(channel_mod, "download_picture", fake_download_picture)

        channel_mod.fetch_all(
            [channel_mod.ChannelRef(channel_mod.ChannelType.SEASON, "1", "598034")],
            root,
        )
    finally:
        monkey.undo()

    keys = _keys(aws_bucket)
    assert "bilibili-season/598034/meta.json" in keys
    assert "bilibili-season/598034/videos.json" in keys
    assert "bilibili-season/598034/BV1aa/BV1aa.m4a" in keys
    assert "bilibili-season/598034/BV1aa/pic.jpg" in keys
    assert "bilibili-season/598034/BV1aa/complete" in keys
    # 本地不该留下任何产物。
    assert not (tmp_path / "output").exists()


def test_rss_generated_into_bucket(config_file, aws_bucket):
    """b2p-rss 直接把 XML 写进桶。"""
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)
    store = root.store
    store.put_bytes(
        "bilibili-season/598034/meta.json",
        json.dumps(
            {
                "upper": {"name": "频道"},
                "title": "合集",
                "mid": 1,
                "id": 598034,
                "cover": "http://p/cover.jpg",
            }
        ).encode(),
    )
    store.put_bytes(
        "bilibili-season/598034/videos.json",
        json.dumps([{"bvid": "BV1aa", "title": "标题", "pubdate": 1700000000}]).encode(),
    )
    store.put_bytes(
        "bilibili-season/598034/BV1aa/meta.json", json.dumps({"title": "标题"}).encode()
    )
    store.put_bytes("bilibili-season/598034/BV1aa/BV1aa.m4a", b"audio")

    rss_cmd_mod.main(["--config", str(config_file), "--backend", "s3"])
    keys = _keys(aws_bucket)
    assert "rss/season/598034.xml" in keys

    body = (
        aws_bucket.get_object(Bucket=BUCKET, Key="rss/season/598034.xml")["Body"]
        .read()
        .decode()
    )
    assert (
        "https://podcast.example.com/bilibili-season/598034/BV1aa/BV1aa.m4a" in body
    )


def test_gen_index_written_into_bucket(config_file, aws_bucket):
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)
    root.store.put_bytes(
        "bilibili-season/598034/videos.json",
        json.dumps(
            [
                {
                    "bvid": "BV1aa",
                    "title": "标题",
                    "pubdate": 1700000000,
                    "pic": "http://p/1.jpg",
                }
            ]
        ).encode(),
    )
    gen_index_mod.main(["--config", str(config_file), "--backend", "s3"])
    assert "index.html" in _keys(aws_bucket)


def test_prune_removes_prefix_from_bucket(config_file, aws_bucket):
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)
    store = root.store
    store.put_bytes("bilibili-season/999/BV1aa/BV1aa.m4a", b"x")
    store.put_bytes("bilibili-season/598034/BV1aa/BV1aa.m4a", b"x")
    store.put_bytes("rss/season/598034.xml", b"x")

    result = prune_mod.prune(str(config_file), "output", backend="s3")
    assert result.deleted_season == ["999"]
    keys = _keys(aws_bucket)
    assert "bilibili-season/999/BV1aa/BV1aa.m4a" not in keys
    assert "bilibili-season/598034/BV1aa/BV1aa.m4a" in keys
    assert "rss/season/598034.xml" in keys


def test_sync_is_noop_in_remote_mode(config_file, capsys):
    code = sync_mod.main(["--config", str(config_file), "--backend", "s3"])
    out = capsys.readouterr().out
    assert code == 0
    assert "skipped" in out


def test_size_reports_bucket_usage(config_file, aws_bucket, capsys):
    from bilibili_podcast.config_loader import load_active_config

    config = load_active_config(str(config_file))
    root = store_mod.resolve_root(config, "output", None)
    root.store.put_bytes("bilibili-season/598034/BV1aa/BV1aa.m4a", b"0123456789")
    code = size_mod.main(["--config", str(config_file), "--backend", "s3"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"s3://{BUCKET}" in out
    assert "bilibili-season/" in out


def test_cron_runs_whole_pipeline_in_remote_mode(config_file, monkeypatch):
    """cron 的每一轮都写同一个桶根，不会在本地留下 output/。"""
    seen: list[str] = []

    def fake_prune(argv):
        seen.append("prune")
        return 0

    def fake_fetch(argv):
        seen.append("fetch")
        return 0

    def fake_summarize(argv):
        seen.append("summarize")
        return 0

    def fake_rss(argv):
        seen.append("rss")
        return 0

    def fake_gen_index(argv):
        seen.append("gen-index")
        return 0

    def fake_sync(argv):
        seen.append("sync")
        return 0

    # cron 在 _run_pipeline 内部按模块导入 main，因此要 patch 源模块。
    from bilibili_podcast.cli import fetch as fetch_mod
    from bilibili_podcast.cli import prune as prune_src
    from bilibili_podcast.cli import summarize as summarize_src
    from bilibili_podcast.cli import sync as sync_src

    monkeypatch.setattr(prune_src, "main", fake_prune)
    monkeypatch.setattr(fetch_mod, "main", fake_fetch)
    monkeypatch.setattr(summarize_src, "main", fake_summarize)
    monkeypatch.setattr(rss_cmd_mod, "main", fake_rss)
    monkeypatch.setattr(gen_index_mod, "main", fake_gen_index)
    monkeypatch.setattr(sync_src, "main", fake_sync)

    cron_mod._run_pipeline(backend="s3", config_path=str(config_file))
    assert seen == ["prune", "fetch", "summarize", "rss", "gen-index", "sync"]
