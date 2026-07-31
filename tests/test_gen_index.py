"""Tests for bilibili_podcast.cli.gen_index."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bilibili_podcast.cli.gen_index import (
    _Entry,
    _load_latest,
    _proxy_cover,
)


def test_proxy_cover_strips_https_and_adds_proxy():
    assert _proxy_cover("https://i0.hdslb.com/bfs/cover/abc.jpg") == \
        "https://images.weserv.nl/?url=i0.hdslb.com/bfs/cover/abc.jpg"


def test_proxy_cover_strips_http():
    assert _proxy_cover("http://example.com/x.jpg") == \
        "https://images.weserv.nl/?url=example.com/x.jpg"


def test_proxy_cover_empty_returns_empty():
    assert _proxy_cover("") == ""


# --- _load_latest ---

def _write_videos(base: Path, kind: str, sid: str, videos: list[dict]) -> None:
    (base / f"bilibili-{kind}" / sid).mkdir(parents=True, exist_ok=True)
    (base / f"bilibili-{kind}" / sid / "videos.json").write_text(
        json.dumps(videos, ensure_ascii=False), encoding="utf-8"
    )


def test_load_latest_picks_highest_pubdate(tmp_path: Path):
    _write_videos(tmp_path, "season", "100", [
        {"bvid": "BVaa", "title": "older", "pubdate": 1700000000, "pic": "https://i.example.com/aa.jpg"},
        {"bvid": "BVbb", "title": "newest", "pubdate": 1700001000, "pic": "https://i.example.com/bb.jpg"},
        {"bvid": "BVcc", "title": "middle", "pubdate": 1700000500, "pic": "https://i.example.com/cc.jpg"},
    ])

    title, date, pic = _load_latest("100", "season", tmp_path)

    assert title == "newest"
    assert date == datetime.fromtimestamp(1700001000, tz=timezone.utc).strftime("%Y-%m-%d")
    assert pic == "https://i.example.com/bb.jpg"


def test_load_latest_returns_empty_when_no_videos_json(tmp_path: Path):
    assert _load_latest("100", "season", tmp_path) == ("", "", "")


def test_load_latest_returns_empty_when_videos_empty(tmp_path: Path):
    _write_videos(tmp_path, "season", "100", [])
    assert _load_latest("100", "season", tmp_path) == ("", "", "")


def test_load_latest_handles_series_kind(tmp_path: Path):
    _write_videos(tmp_path, "series", "200", [
        {"bvid": "BV1", "title": "only", "pubdate": 1700002000, "pic": "https://i.example.com/x.jpg"},
    ])
    title, date, pic = _load_latest("200", "series", tmp_path)
    assert title == "only"
    assert date == datetime.fromtimestamp(1700002000, tz=timezone.utc).strftime("%Y-%m-%d")
    assert pic == "https://i.example.com/x.jpg"


# --- _html latest block ---

def test_html_includes_latest_block_when_entry_has_latest():
    from bilibili_podcast.cli.gen_index import _html

    entry = _Entry(
        kind="season",
        sid="100",
        title="Maki新版认知视频系列",
        author="Maki的完美算术教室",
        cover="https://archive.biliimg.com/bfs/archive/abc.jpg",
        link="https://rss.beauty/rss?url=https://podcast.0xcafebabe.dpdns.org/rss/season/100.xml",
        latest_title="如何拥有从头再来的勇气",
        latest_date="2026-03-02",
    )
    html = _html([entry])

    assert '<div class="latest">' in html
    assert '<div class="latest-label">最新一集</div>' in html
    assert '<div class="latest-title">如何拥有从头再来的勇气</div>' in html
    assert '<div class="latest-date">2026-03-02</div>' in html


def test_html_omits_latest_block_when_no_latest_title():
    from bilibili_podcast.cli.gen_index import _html

    entry = _Entry(
        kind="season",
        sid="100",
        title="Maki新版认知视频系列",
        author="Maki的完美算术教室",
        cover="",
        link="https://rss.beauty/rss?url=https://podcast.0xcafebabe.dpdns.org/rss/season/100.xml",
        latest_title="",
        latest_date="",
    )
    html = _html([entry])

    assert '<div class="latest">' not in html


# --- _load_latest pubdate=0 fallback ---

def test_load_latest_pubdate_zero_yields_empty_date(tmp_path: Path):
    _write_videos(tmp_path, "season", "100", [
        {"bvid": "BV1", "title": "no date", "pubdate": 0},
    ])
    title, date, pic = _load_latest("100", "season", tmp_path)
    assert title == "no date"
    assert date == ""
    assert pic == ""


# --- top banner for rss/new.xml ---

def test_banner_html_links_to_rss_new_xml():
    from bilibili_podcast.cli.gen_index import _banner_html

    html = _banner_html("https://podcast.example.com", [{"uid": "1", "sid": "100", "type": "season"}])

    assert 'class="card banner"' in html
    assert "最新视频合集" in html
    assert 'rss/new.xml' in html
    assert 'href="https://rss.beauty/rss?url=https://podcast.example.com/rss/new.xml"' not in html  # not anchor; we use onclick + copyRSS
    assert "copyRSS('https://rss.beauty/rss?url=https://podcast.example.com/rss/new.xml',this)" in html


def test_html_includes_banner_when_provided():
    from bilibili_podcast.cli.gen_index import _html

    entry = _Entry(
        kind="season", sid="100", title="t", author="a", cover="",
        link="https://rss.beauty/rss?url=https://p.example.com/rss/season/100.xml",
    )
    banner = '<div class="card banner">BANNER</div>'
    html = _html([entry], banner_html=banner)
    assert html.find('class="card banner"') < html.find('class="card">')


def test_html_omits_banner_when_empty():
    from bilibili_podcast.cli.gen_index import _html

    entry = _Entry(
        kind="season", sid="100", title="t", author="a", cover="",
        link="https://rss.beauty/rss?url=https://p.example.com/rss/season/100.xml",
    )
    html = _html([entry], banner_html="")
    assert 'class="card banner"' not in html
    assert 'class="card">' in html


def test_generate_emits_banner_when_new_non_empty(monkeypatch, tmp_path):
    """End-to-end: when sources.json has new entries, the rendered HTML contains the banner."""
    from bilibili_podcast.cli import gen_index as gi

    # Mock _fetch_meta to avoid hitting B站 API
    monkeypatch.setattr(gi, "_fetch_meta", lambda sid, uid, kind: {
        "id": int(sid), "mid": int(uid), "title": f"title-{sid}", "cover": "",
        "upper": {"name": f"up-{uid}"},
    })

    import json
    (tmp_path / "config.yaml").write_text("RSS_URL_PREFIX: https://p.example.com\n", encoding="utf-8")
    (tmp_path / "sources.json").write_text(json.dumps({
        "all": ["https://space.bilibili.com/1/lists/100?type=season"],
        "new": ["https://space.bilibili.com/2/lists/200?type=season"],
    }, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "index.html"
    gi.generate(str(tmp_path / "config.yaml"), str(out))

    html = out.read_text()
    assert 'class="card banner"' in html
    assert "最新视频合集" in html


# --- _load_new_global_latest ---

def test_load_new_global_latest_picks_highest_across_sids(tmp_path: Path):
    from bilibili_podcast.cli.gen_index import _load_new_global_latest
    _write_videos(tmp_path, "new", "100", [
        {"bvid": "BV1", "title": "older", "pubdate": 1700000000, "pic": "https://i.example.com/1.jpg"},
    ])
    _write_videos(tmp_path, "new", "200", [
        {"bvid": "BV2", "title": "middle", "pubdate": 1700000500, "pic": "https://i.example.com/2.jpg"},
        {"bvid": "BV3", "title": "newest", "pubdate": 1700001000, "pic": "https://i.example.com/3.jpg"},
    ])

    title, date, pic = _load_new_global_latest(tmp_path)
    assert title == "newest"
    assert date == datetime.fromtimestamp(1700001000, tz=timezone.utc).strftime("%Y-%m-%d")
    assert pic == "https://i.example.com/3.jpg"


def test_load_new_global_latest_empty_when_no_new_dir(tmp_path: Path):
    from bilibili_podcast.cli.gen_index import _load_new_global_latest
    assert _load_new_global_latest(tmp_path) == ("", "", "")


def test_load_new_global_latest_skips_malformed_videos_json(tmp_path: Path):
    from bilibili_podcast.cli.gen_index import _load_new_global_latest
    _write_videos(tmp_path, "new", "100", [{"bvid": "BV1", "title": "ok", "pubdate": 100, "pic": "p"}])
    # Malformed JSON in sid 200 — must not crash; the valid sid 100 still contributes.
    (tmp_path / "bilibili-new" / "200").mkdir(parents=True)
    (tmp_path / "bilibili-new" / "200" / "videos.json").write_text("not json", encoding="utf-8")

    title, _, _ = _load_new_global_latest(tmp_path)
    assert title == "ok"


# --- banner cover ---

def test_banner_html_includes_cover_when_pic_provided():
    from bilibili_podcast.cli.gen_index import _banner_html

    html = _banner_html(
        "https://p.example.com",
        [{"uid": "1", "sid": "100", "type": "season"}],
        latest_pic="https://archive.biliimg.com/bfs/archive/abc.jpg",
    )

    assert '<div class="card-cover"' in html
    # Goes through images.weserv.nl proxy to avoid hotlink protection
    assert "images.weserv.nl/?url=archive.biliimg.com/bfs/archive/abc.jpg" in html
    # Cover click should open the same rss/new.xml link the copy button uses
    assert "window.open('https://rss.beauty/rss?url=https://p.example.com/rss/new.xml')" in html


def test_banner_html_omits_cover_when_pic_empty():
    from bilibili_podcast.cli.gen_index import _banner_html

    html = _banner_html(
        "https://p.example.com",
        [{"uid": "1", "sid": "100", "type": "season"}],
        latest_pic="",
    )
    assert '<div class="card-cover"' not in html


def test_generate_skips_banner_when_new_empty(monkeypatch, tmp_path):
    from bilibili_podcast.cli import gen_index as gi

    monkeypatch.setattr(gi, "_fetch_meta", lambda sid, uid, kind: {
        "id": int(sid), "mid": int(uid), "title": f"title-{sid}", "cover": "",
        "upper": {"name": f"up-{uid}"},
    })

    import json
    (tmp_path / "config.yaml").write_text("RSS_URL_PREFIX: https://p.example.com\n", encoding="utf-8")
    (tmp_path / "sources.json").write_text(json.dumps({
        "all": ["https://space.bilibili.com/1/lists/100?type=season"],
        "new": [],
    }, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "index.html"
    gi.generate(str(tmp_path / "config.yaml"), str(out))

    html = out.read_text()
    assert 'class="card banner"' not in html
