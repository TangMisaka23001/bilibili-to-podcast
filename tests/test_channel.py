"""Tests for bilibili_podcast.bilibili.channel."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from bilibili_podcast.bilibili.channel import (
    ChannelType,
    ChannelRef,
    _api_type,
    _order,
    _channel_dir,
    fetch_all,
    fetch_one,
    fetch_video_info_with_fallback,
)


SEASON_REF = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
SERIES_REF = ChannelRef(type=ChannelType.SERIES, uid="2", sid="20")


# --- pure helpers ---

def test_api_type_maps_season_to_season_enum():
    from bilibili_api.channel_series import ChannelSeriesType
    assert _api_type(ChannelType.SEASON) is ChannelSeriesType.SEASON


def test_api_type_maps_series_to_series_enum():
    from bilibili_api.channel_series import ChannelSeriesType
    assert _api_type(ChannelType.SERIES) is ChannelSeriesType.SERIES


def test_order_default_for_season():
    from bilibili_api.channel_series import ChannelOrder
    assert _order(ChannelType.SEASON) is ChannelOrder.DEFAULT


def test_order_change_for_series():
    from bilibili_api.channel_series import ChannelOrder
    assert _order(ChannelType.SERIES) is ChannelOrder.CHANGE


def test_channel_dir_season():
    p = _channel_dir(Path("out"), SEASON_REF)
    assert p == Path("out/bilibili-season/10")


def test_channel_dir_series():
    p = _channel_dir(Path("out"), SERIES_REF)
    assert p == Path("out/bilibili-series/20")


# --- fetch_all delegates to fetch_one ---

def test_fetch_all_calls_fetch_one_for_each_ref(tmp_path: Path):
    refs = [SEASON_REF, SERIES_REF]
    with patch("bilibili_podcast.bilibili.channel.fetch_one") as mock_fetch:
        fetch_all(refs, output_root=tmp_path)

    assert mock_fetch.call_count == 2
    mock_fetch.assert_any_call(SEASON_REF, tmp_path)
    mock_fetch.assert_any_call(SERIES_REF, tmp_path)


# --- fetch_one: happy path (season) ---

def test_fetch_one_creates_channel_dir_and_writes_meta(tmp_path: Path):
    fake_meta = {"id": 10, "mid": 1, "title": "test", "cover": "c", "upper": {"name": "u"}}
    fake_video = {"bvid": "BV1xx"}

    with (
        patch("bilibili_podcast.bilibili.channel.fetch_channel_meta", return_value=fake_meta),
        patch("bilibili_podcast.bilibili.channel.fetch_videos", return_value=[fake_video]),
        patch("bilibili_podcast.bilibili.channel.fetch_video_info", return_value={"bvid": "BV1xx", "title": "t", "pic": "p.jpg", "desc": "d"}),
        patch("bilibili_podcast.bilibili.channel.download_audio"),
        patch("bilibili_podcast.bilibili.channel.download_picture"),
        patch("bilibili_podcast.bilibili.channel.write_channel_meta") as wcm,
        patch("bilibili_podcast.bilibili.channel.write_channel_videos") as wcv,
        patch("bilibili_podcast.bilibili.channel.write_video_meta") as wvm,
        patch("bilibili_podcast.bilibili.channel.write_video_complete") as wvc,
        patch("bilibili_podcast.bilibili.channel.has_video_complete", return_value=False),
    ):
        fetch_one(SEASON_REF, tmp_path)

    chdir = tmp_path / "bilibili-season" / "10"
    assert chdir.is_dir()
    wcm.assert_called_once()
    wcv.assert_called_once()
    wvm.assert_called_once()
    wvc.assert_called_once()
    assert chdir / "BV1xx" in chdir.iterdir()


# --- fetch_one: skips completed videos ---

def test_fetch_one_skips_completed_bv(tmp_path: Path):
    with (
        patch("bilibili_podcast.bilibili.channel.fetch_channel_meta", return_value={"id": 10, "mid": 1, "title": "t", "cover": "c", "upper": {"name": "u"}}),
        patch("bilibili_podcast.bilibili.channel.fetch_videos", return_value=[{"bvid": "BVdone"}]),
        patch("bilibili_podcast.bilibili.channel.has_video_complete", return_value=True),
        patch("bilibili_podcast.bilibili.channel.fetch_video_info") as fake_info,
        patch("bilibili_podcast.bilibili.channel.download_audio") as fake_audio,
    ):
        fetch_one(SEASON_REF, tmp_path)

    fake_info.assert_not_called()
    fake_audio.assert_not_called()


# --- fetch_one: per-video error skips the BV but continues ---

def test_fetch_one_continues_after_video_error(tmp_path: Path):
    videos = [{"bvid": "BV1"}, {"bvid": "BV2"}]
    with (
        patch("bilibili_podcast.bilibili.channel.fetch_channel_meta", return_value={"id": 10, "mid": 1, "title": "t", "cover": "c", "upper": {"name": "u"}}),
        patch("bilibili_podcast.bilibili.channel.fetch_videos", return_value=videos),
        patch("bilibili_podcast.bilibili.channel.has_video_complete", return_value=False),
        patch("bilibili_podcast.bilibili.channel.fetch_video_info", side_effect=[RuntimeError("boom"), {"bvid": "BV2", "title": "t2", "pic": "p2.jpg", "desc": ""}]),
        patch("bilibili_podcast.bilibili.channel.download_audio"),
        patch("bilibili_podcast.bilibili.channel.download_picture"),
        patch("bilibili_podcast.bilibili.channel.write_video_complete") as wvc,
    ):
        fetch_one(SEASON_REF, tmp_path)

    # BV1 failed -> no complete written; BV2 succeeded -> complete written once
    assert wvc.call_count == 1


# --- fetch_one: writes complete only after audio + picture ---

def test_fetch_one_writes_complete_after_downloads(tmp_path: Path):
    parent = MagicMock()
    parent.attach_mock(MagicMock(), "download_audio")
    parent.attach_mock(MagicMock(), "download_picture")
    parent.attach_mock(MagicMock(), "write_video_complete")

    with (
        patch("bilibili_podcast.bilibili.channel.fetch_channel_meta", return_value={"id": 10, "mid": 1, "title": "t", "cover": "c", "upper": {"name": "u"}}),
        patch("bilibili_podcast.bilibili.channel.fetch_videos", return_value=[{"bvid": "BV1"}]),
        patch("bilibili_podcast.bilibili.channel.has_video_complete", return_value=False),
        patch("bilibili_podcast.bilibili.channel.fetch_video_info", return_value={"bvid": "BV1", "title": "t", "pic": "p.jpg", "desc": ""}),
        patch("bilibili_podcast.bilibili.channel.download_audio", parent.download_audio),
        patch("bilibili_podcast.bilibili.channel.download_picture", parent.download_picture),
        patch("bilibili_podcast.bilibili.channel.write_video_complete", parent.write_video_complete),
    ):
        fetch_one(SEASON_REF, tmp_path)

    # complete must come after both downloads
    calls = [c[0] for c in parent.mock_calls]
    audio_idx = calls.index("download_audio")
    picture_idx = calls.index("download_picture")
    complete_idx = calls.index("write_video_complete")
    assert audio_idx < complete_idx
    assert picture_idx < complete_idx


# --- fetch_video_info_with_fallback ---

def test_fetch_video_info_with_fallback_returns_api_result_when_api_succeeds(tmp_path: Path):
    """When the Bilibili API call succeeds, fall back to channel_dir is not used."""
    api_info = {"bvid": "BV1", "title": "from-api", "pic": "p.jpg", "desc": ""}
    # Even if a stale videos.json exists with a different title, the API result wins.
    import json
    (tmp_path / "videos.json").write_text(
        json.dumps([{"bvid": "BV1", "title": "from-cache", "pic": "c.jpg"}])
    )

    with patch("bilibili_podcast.bilibili.channel.fetch_video_info", return_value=api_info):
        result = fetch_video_info_with_fallback("BV1", tmp_path)

    assert result["title"] == "from-api"
    assert result["bvid"] == "BV1"


def test_fetch_video_info_with_fallback_uses_videos_json_when_api_raises(tmp_path: Path):
    """When the API raises, return the videos.json record matching the bvid."""
    import json
    record = {
        "bvid": "BV1",
        "aid": 123,
        "title": "cached-title",
        "pic": "http://i1.hdslb.com/bfs/archive/cached.jpg",
        "duration": 1500,
        "pubdate": 1700000000,
    }
    (tmp_path / "videos.json").write_text(
        json.dumps([record, {"bvid": "BVother", "title": "other"}])
    )

    with patch(
        "bilibili_podcast.bilibili.channel.fetch_video_info",
        side_effect=RuntimeError("api down"),
    ):
        result = fetch_video_info_with_fallback("BV1", tmp_path)

    assert result == record


def test_fetch_video_info_with_fallback_raises_when_no_videos_json_record(tmp_path: Path):
    """If the API fails AND the bvid is not in videos.json, re-raise the API error."""
    import json
    (tmp_path / "videos.json").write_text(json.dumps([{"bvid": "BVother"}]))

    with patch(
        "bilibili_podcast.bilibili.channel.fetch_video_info",
        side_effect=RuntimeError("api down"),
    ):
        with pytest.raises(RuntimeError, match="api down"):
            fetch_video_info_with_fallback("BVmissing", tmp_path)
