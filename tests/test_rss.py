"""Tests for bilibili_podcast.rss — RSS XML generation."""
from __future__ import annotations
from __future__ import annotations

import json
from pathlib import Path

import pytest

import bilibili_podcast.rss as rss


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def _setup_season_channel(base: str, sid: str, videos: list[dict]) -> None:
    ch = Path(base) / sid
    _write_json(ch / "meta.json", {
        "id": int(sid),
        "mid": 391930545,
        "title": f"合集 {sid}",
        "cover": f"https://i.example.com/{sid}.jpg",
        "upper": {"name": f"UP{sid}"},
    })
    _write_json(ch / "videos.json", videos)
    for v in videos:
        bv = v["bvid"]
        _write_json(ch / bv / "meta.json", {
            "bvid": bv,
            "title": v["title"],
            "desc": v.get("desc", ""),
            "pic": v.get("pic", ""),
            "duration": v.get("duration", 0),
            "pubdate": v.get("pubdate", 0),
        })


def _setup_series_channel(base: str, sid: str, videos: list[dict]) -> None:
    ch = Path(base) / sid
    _write_json(ch / "meta.json", {
        "series_id": int(sid),
        "mid": 12345,
        "name": f"系列 {sid}",
        "description": f"描述 {sid}",
        "cover": "",
    })
    _write_json(ch / "videos.json", videos)
    for v in videos:
        bv = v["bvid"]
        _write_json(ch / bv / "meta.json", {
            "bvid": bv,
            "title": v["title"],
            "desc": v.get("desc", ""),
            "pic": v.get("pic", ""),
            "duration": v.get("duration", 0),
            "pubdate": v.get("pubdate", 0),
        })


# --- pure helpers ---

def test_channel_sid_list_from_returns_sids():
    assert rss._channel_sid_list_from({"season": [{"sid": 10}, {"sid": 20}]}, "season") == ["10", "20"]


def test_channel_sid_list_from_missing_key_returns_empty():
    assert rss._channel_sid_list_from({}, "season") == []


def test_channel_bilibili_link():
    assert rss._channel_bilibili_link("1", "10") == "https://space.bilibili.com/1/lists/10?type=season"


def test_series_bilibili_link():
    assert rss._series_bilibili_link("2", "20") == "https://space.bilibili.com/2/lists/20?type=series"


def test_timestamp_to_date():
    result = rss._timestamp_to_date(1700000000)
    assert "2023" in result
    assert "GMT" in result


def test_xml_escape_handles_all_five_entities():
    assert rss._xml_escape("a & b < c > d \" e ' f") == \
        "a &amp; b &lt; c &gt; d &quot; e &apos; f"


# --- generate season ---

def test_generate_season_creates_rss_xml(monkeypatch, tmp_path: Path):
    base = tmp_path / "bilibili-season"
    _setup_season_channel(str(base), "598034", [
        {"bvid": "BV1aa", "title": "第一集", "desc": "简介 <>&", "pic": "https://i.example.com/1.jpg", "duration": 123, "pubdate": 1700000000},
        {"bvid": "BV1bb", "title": "第二集", "desc": "简介2", "pic": "https://i.example.com/2.jpg", "duration": 456, "pubdate": 1700001000},
    ])

    monkeypatch.setattr(rss, "season_base_path", str(base) + "/")
    monkeypatch.setattr(rss, "season_rss_path", "season/")
    monkeypatch.setattr(rss, "RSS_URL_PREFIX", "https://podcast.example.com/")
    monkeypatch.setattr(rss, "AUDIO_FORMAT", "m4a")
    monkeypatch.setattr(rss, "bilibili_link_prefix", "https://www.bilibili.com/video/")

    rss.generate({"season": [{"uid": "1", "sid": "598034"}]}, str(tmp_path))

    xml = (tmp_path / "rss" / "season" / "598034.xml").read_text()
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert '<rss version="2.0"' in xml
    assert 'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"' in xml
    assert "<title>合集 598034</title>" in xml
    assert "<itunes:author>UP598034</itunes:author>" in xml
    assert '<itunes:image href="https://i.example.com/598034.jpg" />' in xml
    assert "<title>第一集</title>" in xml
    assert "<title>第二集</title>" in xml
    assert 'enclosure url="https://podcast.example.com/season/598034/BV1aa/BV1aa.m4a"' in xml
    assert "<itunes:duration>123</itunes:duration>" in xml
    # & -> &amp;, < > stay literal in XML text
    assert "<description>简介 &lt;&gt;&amp;</description>" in xml


# --- generate series ---

def test_generate_series_creates_rss_xml(monkeypatch, tmp_path: Path):
    base = tmp_path / "bilibili-series"
    sid = "4891774"
    _setup_series_channel(str(base), sid, [
        {"bvid": "BV2cc", "title": "剧集一", "desc": "desc", "pic": "https://i.example.com/3.jpg", "duration": 789, "pubdate": 1700002000},
    ])
    audio_dir = tmp_path / "output" / "bilibili-series" / sid / "BV2cc"
    audio_dir.mkdir(parents=True)
    (audio_dir / "BV2cc.m4a").write_text("fake-m4a-data" * 500)

    monkeypatch.setattr(rss, "series_base_path", str(base) + "/")
    monkeypatch.setattr(rss, "series_rss_path", "bilibili-series/")
    monkeypatch.setattr(rss, "RSS_URL_PREFIX", "https://podcast.example.com/")
    monkeypatch.setattr(rss, "AUDIO_FORMAT", "m4a")

    config = {"series": [{"uid": "2", "sid": sid}]}
    rss.generate(config, str(tmp_path))

    xml = (tmp_path / "rss" / "series" / f"{sid}.xml").read_text()
    assert "<title>系列 4891774</title>" in xml
    assert "<itunes:author>系列 4891774</itunes:author>"
    assert "<title>剧集一</title>" in xml
    assert 'enclosure url="https://podcast.example.com/bilibili-series/4891774/BV2cc/BV2cc.m4a"' in xml
    assert "<itunes:duration>789</itunes:duration>"


# --- edge cases ---

def test_generate_empty_config_creates_rss_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(rss, "RSS_URL_PREFIX", "https://x/")
    rss.generate({"season": [], "series": []}, str(tmp_path))
    assert (tmp_path / "rss").is_dir()


def test_generate_both_season_and_series(monkeypatch, tmp_path: Path):
    sbase = tmp_path / "bilibili-season"
    _setup_season_channel(str(sbase), "10", [
        {"bvid": "BV3dd", "title": "S1", "desc": "", "pic": "", "duration": 1, "pubdate": 1},
    ])
    xbase = tmp_path / "bilibili-series"
    _setup_series_channel(str(xbase), "20", [
        {"bvid": "BV4ee", "title": "X1", "desc": "", "pic": "", "duration": 2, "pubdate": 2},
    ])
    audio_dir = tmp_path / "output" / "bilibili-series" / "20" / "BV4ee"
    audio_dir.mkdir(parents=True)
    (audio_dir / "BV4ee.m4a").touch()

    monkeypatch.setattr(rss, "season_base_path", str(sbase) + "/")
    monkeypatch.setattr(rss, "season_rss_path", "season/")
    monkeypatch.setattr(rss, "series_base_path", str(xbase) + "/")
    monkeypatch.setattr(rss, "series_rss_path", "bilibili-series/")
    monkeypatch.setattr(rss, "RSS_URL_PREFIX", "https://p.example.com/")
    monkeypatch.setattr(rss, "AUDIO_FORMAT", "m4a")

    rss.generate({
        "season": [{"uid": "1", "sid": "10"}],
        "series": [{"uid": "2", "sid": "20"}],
    }, str(tmp_path))

    assert (tmp_path / "rss" / "season" / "10.xml").exists()
    assert (tmp_path / "rss" / "series" / "20.xml").exists()


# --- _load_json ---

def test_load_json_reads_file(tmp_path: Path):
    p = tmp_path / "test.json"
    p.write_text('{"key": "value"}')
    assert rss._load_json(p) == {"key": "value"}
