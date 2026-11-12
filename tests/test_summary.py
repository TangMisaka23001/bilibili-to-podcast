"""Tests for bilibili_podcast.bilibili.summary — AI 总结归一化与落盘。"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bilibili_podcast.bilibili.summary import (
    CHAPTERS_FILE,
    MAX_CHAPTERS,
    SUMMARY_FILE,
    Chapter,
    OutlineItem,
    Summary,
    chapters_payload,
    format_clock,
    format_timestamp,
    load_summary,
    normalize,
    public_chapters,
    summarize_video,
    write_summary,
)

RAW_OK = {
    "code": 0,
    "model_result": {
        "summary": "七名登山客被困会吃人洋馆。",
        "outline": [
            {
                "title": "开场",
                "timestamp": 1,
                "part_outline": [
                    {"timestamp": 18, "content": "设定介绍"},
                    {"timestamp": 170, "content": "主角背景"},
                ],
            },
            {
                "title": "第二段",
                "timestamp": 601,
                "part_outline": [{"timestamp": 629, "content": "冲突升级"}],
            },
        ],
    },
}


def _video_dir(tmp_path: Path, *, bvid="BV1xx", cid=42, mid=7) -> Path:
    vdir = tmp_path / "bilibili-new" / "100" / bvid
    vdir.mkdir(parents=True)
    (vdir / "meta.json").write_text(
        json.dumps({"bvid": bvid, "cid": cid, "owner": {"mid": mid}}), encoding="utf-8"
    )
    return vdir


# --- normalize ---


def test_normalize_extracts_summary_and_chapters():
    summary = normalize(RAW_OK, fetched_at="2026-11-12T02:00:00+00:00")

    assert summary.source == "bilibili-ai"
    assert summary.available is True
    assert summary.summary == "七名登山客被困会吃人洋馆。"
    assert [c.title for c in summary.chapters] == ["开场", "第二段"]
    assert summary.chapters[0].start == 1
    assert summary.chapters[0].items == (
        OutlineItem(start=18, text="设定介绍"),
        OutlineItem(start=170, text="主角背景"),
    )


def test_normalize_sorts_chapters_and_drops_empty_items():
    raw = {
        "code": 0,
        "model_result": {
            "summary": "s",
            "outline": [
                {"title": "后", "timestamp": 600, "part_outline": [{"timestamp": 601, "content": " "}]},
                {"title": "前", "timestamp": 10},
            ],
        },
    }
    summary = normalize(raw, fetched_at="now")
    assert [c.start for c in summary.chapters] == [10, 600]
    assert summary.chapters[1].items == ()


def test_normalize_marks_none_when_bilibili_has_no_result():
    for raw in (
        {"code": 1, "model_result": {}},
        {"code": -1},
        {"code": 0, "model_result": {"summary": "  ", "outline": []}},
    ):
        summary = normalize(raw, fetched_at="now")
        assert summary.source == "none"
        assert summary.available is False


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(61) == "01:01"
    assert format_timestamp(3671) == "1:01:11"


# --- 落盘与幂等 ---


def test_write_and_load_roundtrip(tmp_path: Path):
    vdir = _video_dir(tmp_path)
    summary = normalize(RAW_OK, fetched_at="2026-11-12T02:00:00+00:00")

    write_summary(vdir, summary, bv="BV1xx")

    assert (vdir / SUMMARY_FILE).is_file()
    assert (vdir / CHAPTERS_FILE).is_file()
    assert load_summary(vdir) == summary


def test_write_removes_stale_chapters_file(tmp_path: Path):
    vdir = _video_dir(tmp_path)
    (vdir / CHAPTERS_FILE).write_text("{}", encoding="utf-8")

    write_summary(vdir, Summary(source="none", code=1), bv="BV1xx")

    assert not (vdir / CHAPTERS_FILE).exists()


def test_load_summary_returns_none_on_broken_json(tmp_path: Path):
    vdir = _video_dir(tmp_path)
    (vdir / SUMMARY_FILE).write_text("not json", encoding="utf-8")
    assert load_summary(vdir) is None


def test_chapters_payload_matches_podcasting2_spec():
    summary = normalize(RAW_OK, fetched_at="now")
    payload = chapters_payload(summary, "BV1xx")

    assert payload["version"] == "1.2.0"
    first = payload["chapters"][0]
    # Apple Podcasts 要求首章 00:00:00
    assert first["startTime"] == 0
    assert first["title"] == "开场"
    assert first["url"] == "https://www.bilibili.com/video/BV1xx?t=0"
    # 其余章节保持原时间
    assert payload["chapters"][1]["startTime"] == 601


def test_public_chapters_clamps_first_to_zero():
    summary = normalize(RAW_OK, fetched_at="now")
    chapters = public_chapters(summary)
    assert chapters[0].start == 0
    assert chapters[1].start == 601


def test_public_chapters_truncates_long_titles():
    summary = Summary(
        source="bilibili-ai",
        summary="s",
        chapters=(Chapter(start=0, title="一" * 80),),
    )
    title = public_chapters(summary)[0].title
    assert len(title) <= 45
    assert title.endswith("…")


def test_public_chapters_caps_count():
    summary = Summary(
        source="bilibili-ai",
        summary="s",
        chapters=tuple(Chapter(start=i * 60, title=f"c{i}") for i in range(40)),
    )
    assert len(public_chapters(summary)) == MAX_CHAPTERS


def test_public_chapters_empty_when_no_chapters():
    assert public_chapters(Summary(source="bilibili-ai", summary="only text")) == ()


def test_format_clock_is_hh_mm_ss():
    assert format_clock(0) == "00:00:00"
    assert format_clock(61) == "00:01:01"
    assert format_clock(3671) == "01:01:11"


# --- summarize_video: 缓存与 TTL ---


def test_summarize_video_fetches_once_then_reuses(tmp_path: Path, monkeypatch):
    from bilibili_podcast.bilibili import summary as mod

    vdir = _video_dir(tmp_path)
    calls: list[tuple] = []

    def fake_fetch(bv, cid, up_mid):
        calls.append((bv, cid, up_mid))
        return RAW_OK

    monkeypatch.setattr(mod, "fetch_conclusion", fake_fetch)

    first = summarize_video(vdir)
    second = summarize_video(vdir)

    assert first is not None and first.available
    assert second == first
    assert calls == [("BV1xx", 42, 7)]


def test_summarize_video_retries_none_after_ttl(tmp_path: Path, monkeypatch):
    from bilibili_podcast.bilibili import summary as mod

    vdir = _video_dir(tmp_path)
    (vdir / SUMMARY_FILE).write_text(
        json.dumps(
            {
                "source": "none",
                "code": 1,
                "fetched_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(mod, "fetch_conclusion", lambda *a: calls.append(a) or RAW_OK)

    summarize_video(vdir)

    assert len(calls) == 1


def test_summarize_video_skips_fresh_none_result(tmp_path: Path, monkeypatch):
    from bilibili_podcast.bilibili import summary as mod

    vdir = _video_dir(tmp_path)
    (vdir / SUMMARY_FILE).write_text(
        json.dumps({"source": "none", "code": 1, "fetched_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(mod, "fetch_conclusion", lambda *a: calls.append(a) or RAW_OK)

    summarize_video(vdir)

    assert calls == []


def test_summarize_video_returns_none_without_meta(tmp_path: Path):
    vdir = tmp_path / "bilibili-new" / "100" / "BVempty"
    vdir.mkdir(parents=True)
    assert summarize_video(vdir) is None


def test_summarize_video_passes_int_ids(tmp_path: Path, monkeypatch):
    from bilibili_podcast.bilibili import summary as mod

    vdir = _video_dir(tmp_path, bvid="BV1yy", cid=99, mid=12345)
    seen: list[tuple] = []
    monkeypatch.setattr(mod, "fetch_conclusion", lambda *a: seen.append(a) or RAW_OK)

    summarize_video(vdir)

    assert seen == [("BV1yy", 99, 12345)]


def test_write_summary_creates_missing_dir(tmp_path: Path):
    vdir = tmp_path / "bilibili-new" / "100" / "BVnew"
    write_summary(vdir, Summary(source="none"), bv="BVnew")
    assert (vdir / SUMMARY_FILE).is_file()


def test_normalize_handles_garbage_outline_entries():
    raw = {"code": 0, "model_result": {"summary": "ok", "outline": ["oops", {"title": ""}]}}
    summary = normalize(raw, fetched_at="now")
    assert summary.source == "bilibili-ai"
    assert summary.chapters == ()


def test_chapter_dataclass_defaults():
    chapter = Chapter(start=5, title="t")
    assert chapter.items == ()
