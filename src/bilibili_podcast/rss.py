"""RSS XML generation for podcast clients."""
from __future__ import annotations

import json
import os
from email.utils import formatdate
from pathlib import Path
from string import Template

from bilibili_podcast.config import (
    AUDIO_FORMAT,
    RSS_URL_PREFIX,
    bilibili_link_prefix,
    season_base_path,
    season_rss_path,
    series_base_path,
    series_rss_path,
)
from bilibili_podcast.logger import get_logger
from bilibili_podcast.xml_template import (
    channel_template,
    feed_xml_template,
    item_template,
)

logger = get_logger()


def _channel_sid_list_from(config: dict, kind: str) -> list[str]:
    return [str(c["sid"]) for c in config.get(kind, [])]


def _channel_dir(kind: str, sid: str) -> str:
    return (Path(season_base_path) if kind == "season" else Path(series_base_path)) / sid


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_season_videos(channel: str) -> list[dict]:
    return _load_json(Path(season_base_path) / channel / "videos.json")


def _load_series_videos(series: str) -> list[dict]:
    return _load_json(Path(series_base_path) / series / "videos.json")


def _load_season_video_meta(channel: str, bv: str) -> dict:
    return _load_json(Path(season_base_path) / channel / bv / "meta.json")


def _load_series_video_meta(series: str, bv: str) -> dict:
    return _load_json(Path(series_base_path) / series / bv / "meta.json")


def _channel_bilibili_link(uid: str, sid: str) -> str:
    return f"https://space.bilibili.com/{uid}/lists/{sid}?type=season"


def _series_bilibili_link(uid: str, sid: str) -> str:
    return f"https://space.bilibili.com/{uid}/lists/{sid}?type=series"


def _timestamp_to_date(timestamp: float) -> str:
    return formatdate(timestamp, localtime=False, usegmt=True)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
    )


def _scan_channel_items(channel: str) -> list[str]:
    items = []
    for video in _load_season_videos(channel):
        bv = video["bvid"]
        video_meta = _load_season_video_meta(channel, bv)
        audio_path = f"{season_rss_path}{channel}/{bv}/{bv}.{AUDIO_FORMAT}"
        items.append(
            Template(item_template).substitute(
                {
                    "title": _xml_escape(video_meta["title"]),
                    "description": _xml_escape(video_meta["desc"]),
                    "image": _xml_escape(video_meta["pic"]),
                    "url": RSS_URL_PREFIX + audio_path,
                    "duration": video_meta["duration"],
                    "length": 0,
                    "link": bilibili_link_prefix + bv,
                    "date": _timestamp_to_date(video_meta["pubdate"]),
                }
            )
        )
    return items


def _scan_series_items(series: str) -> list[str]:
    items = []
    for video in _load_series_videos(series):
        bv = video["bvid"]
        video_dir = Path(series_base_path) / series / bv
        if not video_dir.is_dir():
            continue
        video_meta = _load_series_video_meta(series, bv)
        audio_path = f"{series_rss_path}{series}/{bv}/{bv}.{AUDIO_FORMAT}"
        local_audio = Path("output") / audio_path
        length = local_audio.stat().st_size if local_audio.exists() else 0
        items.append(
            Template(item_template).substitute(
                {
                    "title": _xml_escape(video_meta["title"]),
                    "description": _xml_escape(video_meta["desc"]),
                    "image": _xml_escape(video_meta["pic"]),
                    "url": RSS_URL_PREFIX + audio_path,
                    "duration": video_meta["duration"],
                    "length": length,
                    "link": bilibili_link_prefix + bv,
                    "date": _timestamp_to_date(video_meta["pubdate"]),
                }
            )
        )
    return items


def _channel_xml(channel: str) -> str:
    meta = _load_json(Path(season_base_path) / channel / "meta.json")
    channel_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{channel}.xml",
            "author": meta["upper"]["name"],
            "title": _xml_escape(meta["title"]),
            "description": _xml_escape(meta["title"]),
            "link": _channel_bilibili_link(meta["mid"], meta["id"]),
            "category": "",
            "image": _xml_escape(meta["cover"]),
            "items": "\n".join(_scan_channel_items(channel)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": channel_string})


def _series_xml(series: str) -> str:
    meta = _load_json(Path(series_base_path) / series / "meta.json")
    series_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{series}.xml",
            "author": meta["name"],
            "title": _xml_escape(meta["name"]),
            "description": meta["description"],
            "link": _series_bilibili_link(meta["mid"], meta["series_id"]),
            "category": "",
            "image": "",
            "items": "\n".join(_scan_series_items(series)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": series_string})


def generate(config: dict, output_root: str = "output") -> None:
    Path(output_root, "rss").mkdir(parents=True, exist_ok=True)

    for channel in _channel_sid_list_from(config, "season"):
        Path(output_root, "rss", "season").mkdir(parents=True, exist_ok=True)
        out_path = Path(output_root, "rss", "season", f"{channel}.xml")
        out_path.write_text(_channel_xml(channel), encoding="utf-8")
        logger.info(f"===> wrote {out_path}")

    for series in _channel_sid_list_from(config, "series"):
        Path(output_root, "rss", "series").mkdir(parents=True, exist_ok=True)
        out_path = Path(output_root, "rss", "series", f"{series}.xml")
        out_path.write_text(_series_xml(series), encoding="utf-8")
        logger.info(f"===> wrote {out_path}")
