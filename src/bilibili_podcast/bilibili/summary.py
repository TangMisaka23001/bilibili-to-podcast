"""B站 AI 视频总结 → 播客 description 素材 + Podcasting 2.0 章节文件。

数据源：``x/web-interface/view/conclusion/get``（wbi 签名，**需要登录 Cookie**，
匿名返回 -101）。返回的 ``model_result`` 天然带秒级时间轴：

    {"summary": "...", "outline": [{"title": "...", "timestamp": 1,
      "part_outline": [{"timestamp": 18, "content": "..."}]}]}

每个视频目录落盘两个文件：

* ``summary.json`` —— 归一化后的总结，幂等标记 + TTL 重试用
* ``chapters.json`` —— podcast:chapters 规格（1.2.0），供客户端章节列表使用

取不到总结是常态（实测约 1/3 的视频 B站未生成），此时 source="none"，
RSS 侧回退到 UP 主自己写的简介。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bilibili_api import video as video_api

from bilibili_podcast.bilibili.credential import load_credential
from bilibili_podcast.bilibili.retry import with_retry
from bilibili_podcast.config import bilibili_link_prefix
from bilibili_podcast.logger import get_logger

logger = get_logger()

SUMMARY_FILE = "summary.json"
CHAPTERS_FILE = "chapters.json"
CHAPTERS_VERSION = "1.2.0"

#: 没取到总结的视频，隔这么多天再试一次（B站可能事后补生成）。
RETRY_AFTER_DAYS = 7


@dataclass(frozen=True)
class OutlineItem:
    """一个时间点上的内容摘要（秒）。"""

    start: int
    text: str


@dataclass(frozen=True)
class Chapter:
    """B站给出的一个章节：标题 + 起始秒 + 若干时间点摘要。"""

    start: int
    title: str
    items: tuple[OutlineItem, ...] = ()


@dataclass(frozen=True)
class Summary:
    source: str  # "bilibili-ai" | "none"
    summary: str = ""
    chapters: tuple[Chapter, ...] = ()
    code: int = 0
    fetched_at: str = ""

    @property
    def available(self) -> bool:
        return bool(self.summary or self.chapters)


def format_timestamp(seconds: int) -> str:
    """秒 → ``mm:ss`` / ``h:mm:ss``，用于 description 里的人工可读时间轴。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _as_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def fetch_conclusion(bv: str, cid: int, up_mid: int) -> dict:
    """调用 B站 AI 总结接口；风控错误由 retry 层退避重试。"""
    credential = load_credential()
    video = video_api.Video(bvid=bv, credential=credential)
    result = asyncio.run(
        with_retry(lambda: video.get_ai_conclusion(cid=cid, up_mid=up_mid))
    )
    if not isinstance(result, dict):
        raise TypeError(f"unexpected conclusion response: {type(result).__name__}")
    return result


def normalize(raw: dict, *, fetched_at: str) -> Summary:
    """把接口原始返回压成 Summary；无内容时退化为 source="none"。"""
    code = _as_int(raw.get("code"))
    model = raw.get("model_result") or {}
    text = (model.get("summary") or "").strip()

    chapters: list[Chapter] = []
    for part in model.get("outline") or []:
        if not isinstance(part, dict):
            continue
        title = (part.get("title") or "").strip()
        items = tuple(
            OutlineItem(start=_as_int(item.get("timestamp")), text=(item.get("content") or "").strip())
            for item in (part.get("part_outline") or [])
            if isinstance(item, dict) and (item.get("content") or "").strip()
        )
        if not title and not items:
            continue
        chapters.append(
            Chapter(start=_as_int(part.get("timestamp")), title=title, items=items)
        )
    chapters.sort(key=lambda chapter: chapter.start)

    if not text and not chapters:
        return Summary(source="none", code=code, fetched_at=fetched_at)
    return Summary(
        source="bilibili-ai",
        summary=text,
        chapters=tuple(chapters),
        code=code,
        fetched_at=fetched_at,
    )


def to_payload(summary: Summary) -> dict:
    return {
        "source": summary.source,
        "summary": summary.summary,
        "code": summary.code,
        "fetched_at": summary.fetched_at,
        "chapters": [
            {
                "start": chapter.start,
                "title": chapter.title,
                "items": [{"start": item.start, "text": item.text} for item in chapter.items],
            }
            for chapter in summary.chapters
        ],
    }


def from_payload(data: dict) -> Summary:
    chapters = tuple(
        Chapter(
            start=_as_int(chapter.get("start")),
            title=str(chapter.get("title") or ""),
            items=tuple(
                OutlineItem(start=_as_int(item.get("start")), text=str(item.get("text") or ""))
                for item in (chapter.get("items") or [])
                if isinstance(item, dict) and (item.get("text") or "").strip()
            ),
        )
        for chapter in (data.get("chapters") or [])
        if isinstance(chapter, dict)
    )
    return Summary(
        source=str(data.get("source") or "none"),
        summary=str(data.get("summary") or ""),
        chapters=chapters,
        code=_as_int(data.get("code")),
        fetched_at=str(data.get("fetched_at") or ""),
    )


def chapters_payload(summary: Summary, bv: str) -> dict:
    """podcast:chapters 规范要求的 JSON（startTime 秒 + 标题 + 可跳转链接）。"""
    return {
        "version": CHAPTERS_VERSION,
        "chapters": [
            {
                "startTime": chapter.start,
                "title": chapter.title or format_timestamp(chapter.start),
                "url": f"{bilibili_link_prefix}{bv}?t={chapter.start}",
            }
            for chapter in summary.chapters
        ],
    }


def load_summary(video_dir: Path) -> Summary | None:
    path = video_dir / SUMMARY_FILE
    if not path.is_file():
        return None
    try:
        return from_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"===> ignoring unreadable {path}: {e}")
        return None


def write_summary(video_dir: Path, summary: Summary, *, bv: str) -> None:
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / SUMMARY_FILE).write_text(
        json.dumps(to_payload(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    chapters_path = video_dir / CHAPTERS_FILE
    if summary.chapters:
        chapters_path.write_text(
            json.dumps(chapters_payload(summary, bv), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        chapters_path.unlink(missing_ok=True)


def _is_fresh(summary: Summary, now: datetime, retry_days: int) -> bool:
    if not summary.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(summary.fetched_at)
    except ValueError:
        return False
    return now - fetched < timedelta(days=retry_days)


def summarize_video(
    video_dir: Path,
    *,
    force: bool = False,
    retry_days: int = RETRY_AFTER_DAYS,
    now: datetime | None = None,
) -> Summary | None:
    """抓取并落盘一个视频的总结；已拿到结果时直接复用，避免每轮重复请求。

    返回 None 表示这个目录不是可处理的视频（缺 meta.json 或缺 cid/owner.mid）。
    """
    meta_path = video_dir / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"===> cannot read {meta_path}: {e}")
        return None

    bv = meta.get("bvid")
    cid = meta.get("cid")
    up_mid = (meta.get("owner") or {}).get("mid")
    if not (bv and cid and up_mid):
        logger.warning(f"===> {video_dir.name}: meta.json lacks bvid/cid/owner.mid")
        return None

    now = now or datetime.now(UTC)
    existing = load_summary(video_dir)
    if existing is not None and not force:
        if existing.source == "bilibili-ai":
            return existing
        if _is_fresh(existing, now, retry_days):
            return existing

    raw = fetch_conclusion(str(bv), int(cid), int(up_mid))
    summary = normalize(raw, fetched_at=now.isoformat(timespec="seconds"))
    write_summary(video_dir, summary, bv=str(bv))
    if summary.available:
        logger.info(
            f"===> {bv}: summary ok "
            f"({len(summary.summary)} chars, {len(summary.chapters)} chapters)"
        )
    else:
        logger.info(f"===> {bv}: no AI summary available (code={summary.code})")
    return summary
