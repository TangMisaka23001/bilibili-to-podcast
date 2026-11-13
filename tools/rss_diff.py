#!/usr/bin/env python3
"""Compare two snapshots of the generated RSS tree.

Usage:
    rss_diff.py BEFORE_DIR AFTER_DIR

BEFORE_DIR and AFTER_DIR are two copies of an ``rss/`` directory.  For every
feed the script reports which episodes appeared, which disappeared and which
ones were edited (title, publication date, description, enclosure length or
chapter track).  The weekly cron wrapper uses this to tell the user what
actually landed in the feeds, because a silent "success" tells them nothing.

The comparison is content based: an episode is identified by the BV id found
in its enclosure URL (falling back to the item link), so a feed that keeps the
same episodes but rewrites their descriptions is still reported as a change.
"""
from __future__ import annotations

import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://podcastindex.org/namespace/1.0"
NS = {"itunes": ITUNES_NS, "podcast": PODCAST_NS}


def _bv(*values: str) -> str:
    """Pull the BV id out of a URL so an episode keeps a stable key."""
    for value in values:
        if not value:
            continue
        start = value.find("BV")
        while start != -1:
            tail = value[start + 2:start + 12]
            if len(tail) == 10 and tail.isalnum():
                return value[start:start + 12]
            start = value.find("BV", start + 1)
    return ""


def _text(node: ET.Element, tag: str) -> str:
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse(path: Path) -> dict:
    """Return the feed title and its episodes, keyed by BV id."""
    feed: dict = {"title": "", "items": {}, "error": ""}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        feed["error"] = str(exc)
        return feed
    channel = root.find("channel")
    if channel is None:
        feed["error"] = "no <channel> element"
        return feed
    feed["title"] = _text(channel, "title")
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        url = enclosure.get("url", "") if enclosure is not None else ""
        length = enclosure.get("length", "") if enclosure is not None else ""
        link = _text(item, "link")
        title = _text(item, "title")
        description = _text(item, "description")
        chapters = item.find("podcast:chapters", NS)
        key = _bv(url, link) or title or link
        feed["items"][key] = {
            "title": title,
            "date": _text(item, "pubDate"),
            "length": length,
            "description_md5": hashlib.md5(description.encode("utf-8")).hexdigest(),
            "chapters": chapters.get("url", "") if chapters is not None else "",
        }
    return feed


def _changes(before: dict, after: dict) -> list[str]:
    """Which fields of one episode changed between the two snapshots."""
    changes: list[str] = []
    if before["title"] != after["title"]:
        changes.append("标题")
    if before["date"] != after["date"]:
        changes.append("发布时间")
    if before["length"] != after["length"]:
        changes.append(f"音频大小 {before['length']}→{after['length']}")
    if before["description_md5"] != after["description_md5"]:
        changes.append("简介")
    if before["chapters"] != after["chapters"]:
        changes.append("章节")
    return changes


def _feed_files(root: Path) -> dict:
    if not root.is_dir():
        return {}
    return {p.relative_to(root): p for p in sorted(root.rglob("*.xml")) if p.is_file()}


def _episode_line(key: str, episode: dict) -> str:
    title = episode["title"] or key
    date = episode["date"] or "日期未知"
    return f"  - [{date}] {title}（{key}）"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: rss_diff.py BEFORE_DIR AFTER_DIR", file=sys.stderr)
        return 2
    before_dir = Path(argv[1])
    after_dir = Path(argv[2])
    before_files = _feed_files(before_dir)
    after_files = _feed_files(after_dir)

    all_keys = sorted(set(before_files) | set(after_files))
    if not all_keys:
        print("（没有找到任何 RSS 文件）")
        return 0

    total_new = total_removed = total_changed = 0
    for rel in all_keys:
        before = _parse(before_files[rel]) if rel in before_files else None
        after = _parse(after_dir / rel) if rel in after_files else None

        if before is None:
            print(f"### {rel} · 新增 feed")
        elif after is None:
            print(f"### {rel} · feed 已消失")
        else:
            print(f"### {rel} · {after['title'] or before['title']}")

        if before and before.get("error"):
            print(f"  ⚠ 运行前解析失败：{before['error']}")
        if after and after.get("error"):
            print(f"  ⚠ 运行后解析失败：{after['error']}")
            print()
            continue

        before_items = before["items"] if before else {}
        after_items = after["items"] if after else {}
        added = [k for k in after_items if k not in before_items]
        removed = [k for k in before_items if k not in after_items]
        changed = [k for k in after_items if k in before_items
                   and _changes(before_items[k], after_items[k])]

        total_new += len(added)
        total_removed += len(removed)
        total_changed += len(changed)

        if added:
            print(f"  新增 {len(added)} 集")
            for key in added:
                print(_episode_line(key, after_items[key]))
        if removed:
            print(f"  删除 {len(removed)} 集")
            for key in removed:
                print(_episode_line(key, before_items[key]))
        if changed:
            print(f"  变更 {len(changed)} 集")
            for key in changed:
                fields = "、".join(_changes(before_items[key], after_items[key]))
                print(f"  - [{after_items[key]['date'] or '日期未知'}] "
                      f"{after_items[key]['title'] or key}（{key}）→ {fields}")
        if not added and not removed and not changed:
            print("  无变化")
        print()

    print(f"合计：新增 {total_new} 集，删除 {total_removed} 集，变更 {total_changed} 集")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
