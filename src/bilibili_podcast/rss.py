"""RSS XML generation for podcast clients.

Every path derives from one ``output_root``: a file's bucket key is exactly its
path relative to that root, so the public URL is ``RSS_URL_PREFIX + <rel path>``.
Keeping that invariant in one place is why this module has no path constants.
"""
from __future__ import annotations

import json
from email.utils import formatdate
from pathlib import Path
from string import Template

from bilibili_podcast.bilibili.summary import (
    CHAPTERS_FILE,
    Summary,
    format_timestamp,
    load_summary,
)
from bilibili_podcast.config import (
    AUDIO_FORMAT,
    RSS_URL_PREFIX,
    bilibili_link_prefix,
)
from bilibili_podcast.logger import get_logger
from bilibili_podcast.xml_template import (
    channel_template,
    feed_xml_template,
    item_template,
)

logger = get_logger()

#: Directory name per feed kind. The same string is the R2 key prefix.
_KIND_DIR = {
    "season": "bilibili-season",
    "series": "bilibili-series",
    "new": "bilibili-new",
}

#: Container -> MIME type for ``<enclosure type>``. A wrong value makes some
#: clients hand the file to the wrong demuxer, so it must follow AUDIO_FORMAT.
_AUDIO_MIME = {
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
}


def _audio_mime() -> str:
    return _AUDIO_MIME.get(AUDIO_FORMAT.lower(), "application/octet-stream")


def _channel_sid_list_from(config: dict, kind: str) -> list[str]:
    return [str(c["sid"]) for c in config.get(kind, [])]


def _channel_dir(root: Path, kind: str, sid: str) -> Path:
    return root / _KIND_DIR[kind] / sid


def _rel(kind: str, *parts: str) -> str:
    return "/".join((_KIND_DIR[kind], *parts))


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_dict(path: Path) -> dict:
    """Read a JSON object; a wrong shape is a loud error, not a silent skip."""
    data = _load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return data


def _load_list(path: Path) -> list[dict]:
    """Read a JSON array of objects; a wrong shape is a loud error."""
    data = _load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array: {path}")
    return data


def _load_videos(root: Path, kind: str, sid: str) -> list[dict]:
    return _load_list(_channel_dir(root, kind, sid) / "videos.json")


def _load_video_meta(root: Path, kind: str, sid: str, bv: str) -> dict:
    return _load_dict(_channel_dir(root, kind, sid) / bv / "meta.json")


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


#: description 里给章节明细的字数预算，超出后只保留章节标题。
_DESCRIPTION_BUDGET = 1200


def _description_with_summary(bv: str, summary: Summary) -> str:
    """AI 总结 + 章节时间轴 + 原视频链接（纯文本，客户端渲染最稳）。"""
    parts: list[str] = []
    if summary.summary:
        parts.append(summary.summary)
    if summary.chapters:
        lines = ["章节"]
        budget = _DESCRIPTION_BUDGET
        for chapter in summary.chapters:
            heading = f"{format_timestamp(chapter.start)} {chapter.title}".rstrip()
            lines.append(heading)
            budget -= len(heading)
            for item in chapter.items:
                detail = f"  · {format_timestamp(item.start)} {item.text}"
                if budget - len(detail) < 0:
                    break
                lines.append(detail)
                budget -= len(detail)
        parts.append("\n".join(lines))
    parts.append(f"原视频：{bilibili_link_prefix}{bv}")
    parts.append("（总结与章节由 B站 AI 生成）")
    return "\n\n".join(parts)


def _render_item(
    root: Path,
    kind: str,
    sid: str,
    bv: str,
    video_meta: dict,
    summary: Summary | None = None,
) -> str:
    rel_audio = _rel(kind, sid, bv, f"{bv}.{AUDIO_FORMAT}")
    local_audio = root / rel_audio
    length = local_audio.stat().st_size if local_audio.exists() else 0

    description = video_meta.get("desc", "")
    chapters_tag = ""
    if summary is not None and summary.available:
        description = _description_with_summary(bv, summary)
        if summary.chapters:
            rel_chapters = _rel(kind, sid, bv, CHAPTERS_FILE)
            chapters_tag = (
                f'\n<podcast:chapters url="{RSS_URL_PREFIX}{rel_chapters}"'
                f' type="application/json+chapters"/>'
            )

    return Template(item_template).substitute(
        {
            "title": _xml_escape(video_meta.get("title", bv)),
            "description": _xml_escape(description),
            "image": _xml_escape(video_meta.get("pic", "")),
            "url": RSS_URL_PREFIX + rel_audio,
            "duration": video_meta.get("duration", 0),
            "length": length,
            "audio_type": _audio_mime(),
            "chapters": chapters_tag,
            "link": bilibili_link_prefix + bv,
            "date": _timestamp_to_date(video_meta.get("pubdate", 0)),
        }
    )


def _scan_items(root: Path, kind: str, sid: str) -> list[str]:
    """Items for one season/series sid, skipping BVs whose meta.json is missing."""
    items: list[str] = []
    for video in _load_videos(root, kind, sid):
        bv = video["bvid"]
        try:
            video_meta = _load_video_meta(root, kind, sid, bv)
        except FileNotFoundError:
            logger.warning(f"===> {kind}: skipping {sid}/{bv} (meta.json missing)")
            continue
        items.append(_render_item(root, kind, sid, bv, video_meta))
    return items


def _channel_xml(root: Path, channel: str) -> str:
    meta = _load_dict(_channel_dir(root, "season", channel) / "meta.json")
    channel_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{channel}.xml",
            "author": meta["upper"]["name"],
            "title": _xml_escape(meta["title"]),
            "description": _xml_escape(meta["title"]),
            "link": _channel_bilibili_link(meta["mid"], meta["id"]),
            "category": "",
            "image": _xml_escape(meta["cover"]),
            "items": "\n".join(_scan_items(root, "season", channel)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": channel_string})


def _series_xml(root: Path, series: str) -> str:
    meta = _load_dict(_channel_dir(root, "series", series) / "meta.json")
    series_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{series}.xml",
            "author": meta["name"],
            "title": _xml_escape(meta["name"]),
            "description": meta["description"],
            "link": _series_bilibili_link(meta["mid"], meta["series_id"]),
            "category": "",
            "image": "",
            "items": "\n".join(_scan_items(root, "series", series)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": series_string})


def _has_complete(root: Path, sid: str, bv: str) -> bool:
    """Whether the BV has a `complete` sentinel, meaning fetch succeeded."""
    return (_channel_dir(root, "new", sid) / bv / "complete").exists()


def _scan_new_items(root: Path, sids: list[str], top_n: int = 5) -> list[str]:
    """Merge the top-N newest videos across the given new-sids, return RSS item XML.

    For each sid, take the top_n by videos.json pubdate desc; then merge
    across all sids and re-sort by pubdate desc so the merged feed is also
    newest-first. BVs without a `complete` sentinel (fetch was interrupted /
    failed for that BV) are skipped silently — they have no local m4a so
    emitting an RSS item would point to a 404 on R2.
    """
    candidates: list[tuple[int, dict, str]] = []  # (pubdate, video, sid)
    for sid in sids:
        videos = _load_videos(root, "new", sid)
        sorted_videos = sorted(videos, key=lambda v: v.get("pubdate", 0), reverse=True)
        for v in sorted_videos[:top_n]:
            candidates.append((v.get("pubdate", 0), v, sid))

    candidates.sort(key=lambda c: c[0], reverse=True)

    items: list[str] = []
    for _pubdate, video, sid in candidates:
        bv = video["bvid"]
        if not _has_complete(root, sid, bv):
            logger.warning(
                f"===> new: skipping {sid}/{bv} (no complete marker, fetch failed)"
            )
            continue
        try:
            video_meta = _load_video_meta(root, "new", sid, bv)
        except FileNotFoundError:
            logger.warning(f"===> new: skipping {sid}/{bv} (meta.json missing)")
            continue
        summary = load_summary(_channel_dir(root, "new", sid) / bv)
        items.append(_render_item(root, "new", sid, bv, video_meta, summary))
    return items


def _new_xml(root: Path, sids: list[str], uids: list[str], kinds: list[str], top_n: int = 5) -> str:
    """Build the merged rss/new.xml.

    Channel-level fields (author / image / link) are taken from the first
    sid; items are merged across all sids.
    """
    items = _scan_new_items(root, sids, top_n=top_n)

    if sids:
        first_meta = _load_dict(_channel_dir(root, "new", sids[0]) / "meta.json")
        first_kind = kinds[0]
        first_uid = uids[0]
        author = first_meta.get("upper", {}).get("name") or "Unknown"
        title = "最新视频合集"
        description = title
        link = (
            _channel_bilibili_link(first_uid, sids[0])
            if first_kind == "season"
            else _series_bilibili_link(first_uid, sids[0])
        )
        image = first_meta.get("cover") or first_meta.get("upper", {}).get("face", "")
    else:
        author = ""
        title = "最新视频合集"
        description = title
        link = ""
        image = ""

    channel_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/new.xml",
            "author": _xml_escape(author),
            "title": _xml_escape(title),
            "description": _xml_escape(description),
            "link": link,
            "category": "",
            "image": _xml_escape(image),
            "items": "\n".join(items),
        }
    )
    return Template(feed_xml_template).substitute({"channel": channel_string})


def generate(config: dict, output_root: str | Path = "output", top_n: int = 5) -> None:
    root = Path(output_root)
    rss_dir = root / "rss"
    rss_dir.mkdir(parents=True, exist_ok=True)

    for channel in _channel_sid_list_from(config, "season"):
        (rss_dir / "season").mkdir(parents=True, exist_ok=True)
        out_path = rss_dir / "season" / f"{channel}.xml"
        out_path.write_text(_channel_xml(root, channel), encoding="utf-8")
        logger.info(f"===> wrote {out_path}")

    for series in _channel_sid_list_from(config, "series"):
        (rss_dir / "series").mkdir(parents=True, exist_ok=True)
        out_path = rss_dir / "series" / f"{series}.xml"
        out_path.write_text(_series_xml(root, series), encoding="utf-8")
        logger.info(f"===> wrote {out_path}")

    new_refs = config.get("new", [])
    if new_refs:
        sids = [str(c["sid"]) for c in new_refs]
        uids = [str(c["uid"]) for c in new_refs]
        kinds = [c["type"] for c in new_refs]
        out_path = rss_dir / "new.xml"
        out_path.write_text(
            _new_xml(root, sids, uids, kinds, top_n=top_n), encoding="utf-8"
        )
        logger.info(f"===> wrote {out_path}")
