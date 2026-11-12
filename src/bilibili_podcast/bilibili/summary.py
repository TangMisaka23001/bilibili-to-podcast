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
import hashlib
import json
import subprocess
import tempfile
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
#: 内嵌章节完成标记，内容是章节 payload 的 md5——章节变了就重新 mux。
EMBED_MARKER = "chapters.embedded"
CHAPTERS_VERSION = "1.2.0"

#: 没取到总结的视频，隔这么多天再试一次（B站可能事后补生成）。
RETRY_AFTER_DAYS = 7

#: Apple Podcasts 对章节的硬性/建议要求（podcasters.apple.com/support/5482）：
#: 首章必须从 00:00:00 开始，标题不超过 45 字符，每小时不超过 6 章。
CHAPTER_TITLE_LIMIT = 45
MAX_CHAPTERS = 12


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
    """秒 → ``mm:ss`` / ``h:mm:ss``。"""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_clock(seconds: int) -> str:
    """秒 → ``HH:MM:SS``。

    Apple Podcasts 解析 episode description 里的章节时按这个格式识别，
    并且要求第一章写成 ``00:00:00``。
    """
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def public_chapters(
    summary: Summary,
    *,
    max_chapters: int = MAX_CHAPTERS,
    title_limit: int = CHAPTER_TITLE_LIMIT,
) -> tuple[Chapter, ...]:
    """客户端展示用的章节列表：首章归零、标题截断、数量收敛。

    B站给出的第一个章节通常在 1~20 秒，Apple Podcasts 会因此认为
    「首章不是 00:00:00」而不解析 description 里的章节，所以这里统一归零。
    """
    if not summary.chapters:
        return ()
    kept = list(summary.chapters[:max_chapters])
    out: list[Chapter] = []
    for index, chapter in enumerate(kept):
        title = chapter.title.strip()
        if len(title) > title_limit:
            title = title[: title_limit - 1].rstrip() + "…"
        start = 0 if index == 0 else chapter.start
        out.append(Chapter(start=start, title=title, items=chapter.items))
    return tuple(out)


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
    """podcast:chapters 规范要求的 JSON（startTime 秒 + 标题 + 可跳转链接）。

    内容与 description 里的章节列表保持一致，首章同样归零。
    """
    return {
        "version": CHAPTERS_VERSION,
        "chapters": [
            {
                "startTime": chapter.start,
                "title": chapter.title or format_timestamp(chapter.start),
                "url": f"{bilibili_link_prefix}{bv}?t={chapter.start}",
            }
            for chapter in public_chapters(summary)
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


def _ffmetadata(summary: Summary, duration: float) -> str:
    """生成 ffmpeg 的 FFMETADATA 文本（章节写入 MP4 chapter track）。"""
    chapters = public_chapters(summary)
    lines = [";FFMETADATA1"]
    for index, chapter in enumerate(chapters):
        start = float(chapter.start)
        end = float(chapters[index + 1].start) if index + 1 < len(chapters) else duration
        if end <= start:
            continue
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(start * 1000)}",
            f"END={int(end * 1000)}",
            f"title={chapter.title}",
        ]
    return "\n".join(lines) + "\n"


def _audio_duration(path: Path) -> float:
    """用 ffprobe 取音频时长（秒）；失败返回 0。"""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        )
        return float(out.stdout.strip() or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def embed_chapters(video_dir: Path, bv: str, audio_format: str) -> bool:
    """把章节写进音频文件本身（MP4 chapter track）。

    RSS 的 `<podcast:chapters>` 只有 iOS/iPadOS 26.2+ 才读，文件内嵌章节从
    2005 年起所有版本都认——这是「别人的播客都能看到时间轴」的原因。
    只 remux 不重编码，返回 True 表示文件被改写。
    """
    audio = video_dir / f"{bv}.{audio_format}"
    summary = load_summary(video_dir)
    if summary is None or not summary.chapters or not audio.is_file():
        return False

    payload = json.dumps(chapters_payload(summary, bv), ensure_ascii=False)
    marker = video_dir / EMBED_MARKER
    digest = hashlib.new("md5", payload.encode("utf-8"), usedforsecurity=False).hexdigest()
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == digest:
        return False

    duration = _audio_duration(audio)
    if duration <= 0:
        logger.warning(f"===> {bv}: cannot read audio duration, skip chapter embedding")
        return False

    with tempfile.TemporaryDirectory(prefix="b2p-chapters-") as tmpdir:
        meta_path = Path(tmpdir) / "chapters.txt"
        meta_path.write_text(_ffmetadata(summary, duration), encoding="utf-8")
        out_tmp = audio.with_suffix(".tmp" + audio.suffix)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", str(audio), "-i", str(meta_path),
                    "-map", "0", "-map_metadata", "1", "-map_chapters", "1",
                    "-c", "copy", str(out_tmp),
                ],
                check=True,
                timeout=600,
            )
            out_tmp.replace(audio)
        except (OSError, subprocess.SubprocessError) as e:
            out_tmp.unlink(missing_ok=True)
            logger.error(f"===> {bv}: failed to embed chapters: {e}")
            return False

    marker.write_text(digest, encoding="utf-8")
    logger.info(f"===> {bv}: embedded {len(public_chapters(summary))} chapters into audio")
    return True


def _write_if_changed(path: Path, text: str) -> bool:
    """内容一致就不重写——避免每轮跑出新的 mtime/size 触发 R2 重传。"""
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except OSError as e:
            logger.debug(f"===> cannot read {path} for comparison, rewriting: {e}")
    path.write_text(text, encoding="utf-8")
    return True


def write_summary(video_dir: Path, summary: Summary, *, bv: str) -> None:
    """落盘 summary.json / chapters.json；渲染规则变化时下一次运行会自愈。"""
    video_dir.mkdir(parents=True, exist_ok=True)
    _write_if_changed(
        video_dir / SUMMARY_FILE,
        json.dumps(to_payload(summary), ensure_ascii=False, indent=2),
    )
    chapters_path = video_dir / CHAPTERS_FILE
    if summary.chapters:
        _write_if_changed(
            chapters_path,
            json.dumps(chapters_payload(summary, bv), ensure_ascii=False, indent=2),
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
            # 复用已有结果，但按当前渲染规则重写产物（章节归零/截断等改动可自愈）
            write_summary(video_dir, existing, bv=str(bv))
            return existing
        if _is_fresh(existing, now, retry_days):
            write_summary(video_dir, existing, bv=str(bv))
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
