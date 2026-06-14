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

from bilibili_podcast.bilibili.audio import download_audio, download_picture
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


def fetch_channel_meta(ref: ChannelRef) -> dict:
    series = ChannelSeries(id_=ref.sid, uid=ref.uid, type_=_api_type(ref.type))
    return asyncio.run(series.get_meta())


async def _fetch_videos_async(ref: ChannelRef, meta: dict) -> list[dict]:
    series = ChannelSeries(id_=ref.sid, uid=ref.uid, type_=_api_type(ref.type))
    pn = 1
    archives: list[dict] = []
    total_key = "media_count" if ref.type == ChannelType.SEASON else "total"
    while True:
        page = await series.get_videos(sort=_order(ref.type), pn=pn)
        archives += page["archives"]
        if len(archives) >= meta[total_key]:
            break
        pn += 1
    return archives


def fetch_videos(ref: ChannelRef, meta: dict) -> list[dict]:
    return asyncio.run(_fetch_videos_async(ref, meta))


def fetch_video_info(bv: str) -> dict:
    info = asyncio.run(video_api.Video(bvid=bv).get_info())
    info.pop("ugc_season", None)
    return info


def fetch_one(ref: ChannelRef, output_root: Path) -> None:
    channel_dir = _channel_dir(output_root, ref)
    channel_dir.mkdir(parents=True, exist_ok=True)

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
        info = fetch_video_info(bv)
        write_video_meta(vdir, info)
        download_audio(ref, bv, vdir)
        download_picture(info["pic"], vdir / "pic.jpg")
        write_video_complete(vdir)
        logger.info(f"===> finished {bv}")


def fetch_all(refs: list[ChannelRef], output_root: Path = Path("output")) -> None:
    for ref in refs:
        fetch_one(ref, output_root)
