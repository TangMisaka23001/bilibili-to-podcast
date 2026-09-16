"""Channel (Season/Series) handling: meta fetch, video listing, dispatch."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from bilibili_api import video as video_api
from bilibili_api.channel_series import (
    ChannelOrder,
    ChannelSeries,
    ChannelSeriesType,
)
from bilibili_api.exceptions import NetworkException, ResponseCodeException

from bilibili_podcast.bilibili.audio import download_audio, download_picture
from bilibili_podcast.bilibili.credential import load_credential
from bilibili_podcast.bilibili.meta import (
    has_video_complete,
    write_channel_meta,
    write_channel_videos,
    write_video_complete,
    write_video_meta,
)
from bilibili_podcast.logger import get_logger

logger = get_logger()


class ChannelType(str, Enum):
    SEASON = "season"
    SERIES = "series"


@dataclass(frozen=True)
class ChannelRef:
    type: ChannelType
    uid: str
    sid: str


#: Retry policy for risk-control responses (HTTP 412 / API -352).
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
_RETRYABLE_HTTP_STATUS = frozenset({412, 429})
_RETRYABLE_API_CODES = frozenset({-352, -412})

#: Fields a cached videos.json record must carry to stand in for a video detail.
_REQUIRED_VIDEO_FIELDS = ("bvid", "title", "pic")

_credential_warned = False


def _warn_if_no_credential() -> None:
    """Log once if no usable login cookie is configured: the detail API will 412."""
    global _credential_warned
    if _credential_warned:
        return
    _credential_warned = True
    cred = load_credential()
    if cred is None or not cred.sessdata:
        logger.warning(
            "===> no SESSDATA in B2P_COOKIE_CONTENT/./cookie; the video detail API "
            "will return HTTP 412 — re-export a logged-in cookie"
        )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, NetworkException):
        return exc.status in _RETRYABLE_HTTP_STATUS
    if isinstance(exc, ResponseCodeException):
        return exc.code in _RETRYABLE_API_CODES
    return False


async def _with_retry(call, *, attempts=RETRY_ATTEMPTS, base_delay=RETRY_BASE_DELAY, sleep=asyncio.sleep):
    """Await ``call()``, retrying risk-control errors with exponential backoff."""
    for attempt in range(attempts):
        try:
            return await call()
        except Exception as exc:
            if not _is_retryable(exc):
                raise
            if attempt == attempts - 1:
                logger.error(f"===> risk control persisted after {attempts} attempts: {exc}")
                raise
            delay = base_delay * (2**attempt)
            logger.warning(f"===> risk control, retrying in {delay:.0f}s: {exc}")
            await sleep(delay)


def _make_series(ref: ChannelRef) -> ChannelSeries:
    return ChannelSeries(
        id_=ref.sid,
        uid=ref.uid,
        type_=_api_type(ref.type),
        credential=load_credential(),
    )


def _api_type(t: ChannelType) -> ChannelSeriesType:
    return (
        ChannelSeriesType.SEASON
        if t == ChannelType.SEASON
        else ChannelSeriesType.SERIES
    )


def _order(t: ChannelType) -> ChannelOrder:
    return (
        ChannelOrder.DEFAULT
        if t == ChannelType.SEASON
        else ChannelOrder.CHANGE
    )


def _channel_dir(output_root: Path, ref: ChannelRef) -> Path:
    return output_root / f"bilibili-{ref.type.value}" / ref.sid


def _new_dir(output_root: Path, ref: ChannelRef) -> Path:
    return output_root / "bilibili-new" / ref.sid


def fetch_channel_meta(ref: ChannelRef) -> dict:
    series = _make_series(ref)
    return asyncio.run(_with_retry(series.get_meta))


async def _fetch_videos_async(ref: ChannelRef, meta: dict) -> list[dict]:
    series = _make_series(ref)
    pn = 1
    archives: list[dict] = []
    total_key = "media_count" if ref.type == ChannelType.SEASON else "total"
    while True:
        page = await _with_retry(
            lambda pn=pn: series.get_videos(sort=_order(ref.type), pn=pn)
        )
        archives += page["archives"]
        if len(archives) >= meta[total_key]:
            break
        pn += 1
    return archives


def fetch_videos(ref: ChannelRef, meta: dict) -> list[dict]:
    return asyncio.run(_fetch_videos_async(ref, meta))


def fetch_video_info(bv: str) -> dict:
    credential = load_credential()
    info = asyncio.run(
        _with_retry(lambda: video_api.Video(bvid=bv, credential=credential).get_info())
    )
    info.pop("ugc_season", None)
    return info


def _load_video_record_from_cache(channel_dir: Path, bv: str) -> dict | None:
    """Find a videos.json record matching `bv` in `channel_dir`."""
    import json

    videos_path = channel_dir / "videos.json"
    if not videos_path.exists():
        return None
    records = json.loads(videos_path.read_text())
    for record in records:
        if record.get("bvid") == bv:
            return record
    return None


def fetch_video_info_with_fallback(bv: str, channel_dir: Path) -> dict:
    """Fetch video info, falling back to channel_dir/videos.json on API failure."""
    try:
        return fetch_video_info(bv)
    except Exception as e:
        cached = _load_video_record_from_cache(channel_dir, bv)
        if cached is None:
            raise
        missing = [f for f in _REQUIRED_VIDEO_FIELDS if not cached.get(f)]
        if missing:
            logger.warning(
                f"===> api failed for {bv} and cached videos.json record lacks "
                f"{missing}, not usable as fallback: {e}"
            )
            raise
        logger.warning(f"===> api failed for {bv}, using cached videos.json record: {e}")
        return cached


def fetch_one(ref: ChannelRef, output_root: Path) -> None:
    channel_dir = _channel_dir(output_root, ref)
    channel_dir.mkdir(parents=True, exist_ok=True)
    _warn_if_no_credential()

    meta = fetch_channel_meta(ref)
    write_channel_meta(channel_dir, meta)
    logger.info(f"===> wrote channel meta for {ref}")

    videos = fetch_videos(ref, meta)
    write_channel_videos(channel_dir, videos)

    for video in videos:
        bv = video["bvid"]
        if has_video_complete(channel_dir, bv):
            logger.info(f"===> {bv} complete, skipping")
            continue
        vdir = channel_dir / bv
        vdir.mkdir(parents=True, exist_ok=True)
        try:
            info = fetch_video_info_with_fallback(bv, channel_dir)
            write_video_meta(vdir, info)
            download_audio(ref, bv, vdir)
            download_picture(info["pic"], vdir / "pic.jpg")
            write_video_complete(vdir)
            logger.info(f"===> finished {bv}")
        except Exception as e:
            logger.error(f"===> failed {bv}: {e}")


def fetch_new(ref: ChannelRef, output_root: Path, top_n: int = 5) -> None:
    """Fetch only the newest `top_n` videos of the channel into bilibili-new/.

    Always re-evaluates the top-N (sorted by videos.json pubdate desc). Already
    complete entries are skipped; entries that fell out of the top-N are pruned
    so the directory only ever holds the current top-N.
    """
    import shutil

    channel_dir = _new_dir(output_root, ref)
    channel_dir.mkdir(parents=True, exist_ok=True)
    _warn_if_no_credential()

    meta = fetch_channel_meta(ref)
    write_channel_meta(channel_dir, meta)
    logger.info(f"===> wrote new-channel meta for {ref}")

    videos = fetch_videos(ref, meta)
    write_channel_videos(channel_dir, videos)

    sorted_videos = sorted(videos, key=lambda v: v.get("pubdate", 0), reverse=True)
    top_bvs = {v["bvid"] for v in sorted_videos[:top_n]}

    # Prune any bv directory not in the new top-N
    for entry in sorted(channel_dir.iterdir()):
        if entry.name in top_bvs or not entry.is_dir():
            continue
        shutil.rmtree(entry)
        logger.info(f"===> pruned {entry.name} (no longer in top {top_n})")

    for video in sorted_videos[:top_n]:
        bv = video["bvid"]
        if has_video_complete(channel_dir, bv):
            logger.info(f"===> new: {bv} complete, skipping")
            continue
        vdir = channel_dir / bv
        vdir.mkdir(parents=True, exist_ok=True)
        try:
            info = fetch_video_info_with_fallback(bv, channel_dir)
            write_video_meta(vdir, info)
            download_audio(ref, bv, vdir)
            download_picture(info["pic"], vdir / "pic.jpg")
            write_video_complete(vdir)
            logger.info(f"===> new: finished {bv}")
        except Exception as e:
            logger.error(f"===> new: failed {bv}: {e}")


def fetch_all_new(refs: list[ChannelRef], output_root: Path = Path("output"), top_n: int = 5) -> None:
    for ref in refs:
        fetch_new(ref, output_root, top_n=top_n)


def fetch_all(refs: list[ChannelRef], output_root: Path = Path("output")) -> None:
    for ref in refs:
        fetch_one(ref, output_root)
