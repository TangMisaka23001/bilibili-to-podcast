"""Read/write channel and video metadata JSON files."""
from __future__ import annotations

import json
from pathlib import Path

from bilibili_podcast.config import AUDIO_FORMAT
from bilibili_podcast.store import StorePath

#: 本地后端是 ``pathlib.Path``，对象存储后端是 ``S3Path``。
#: 两者共用同一套接口（``/``、``write_text``、``touch``、``exists``、
#: ``is_file``、``stat().st_size``），因此同一份代码可以在两种后端上跑。
PathLike = Path | StorePath


def write_channel_meta(channel_dir: PathLike, meta: dict) -> None:
    (channel_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_channel_videos(channel_dir: PathLike, videos: list[dict]) -> None:
    (channel_dir / "videos.json").write_text(
        json.dumps(videos, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_video_meta(video_dir: PathLike, info: dict) -> None:
    (video_dir / "meta.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_video_complete(video_dir: PathLike) -> None:
    (video_dir / "complete").touch()


def video_audio_path(video_dir: PathLike) -> PathLike:
    """Where a BV's audio lives — the file the RSS enclosure points at."""
    return video_dir / f"{video_dir.name}.{AUDIO_FORMAT}"


def has_video_audio(video_dir: PathLike) -> bool:
    """Whether a BV's audio file is really there.

    A zero-byte file counts as missing: it cannot be uploaded as a real
    episode or played by a podcast client, and every RSS enclosure that
    points at it is a broken link.
    """
    path = video_audio_path(video_dir)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def has_video_complete(channel_dir: PathLike, bv: str) -> bool:
    """Whether this BV is fully materialised and can be skipped.

    The ``complete`` sentinel alone is not enough: it can outlive the audio
    (the marker was published while the audio upload failed, or a pull
    restored the marker without its audio). Trusting such a marker would
    publish an RSS enclosure pointing at a file that does not exist, so the
    audio itself has to be there.
    """
    video_dir = channel_dir / bv
    if not (video_dir / "complete").exists():
        return False
    return has_video_audio(video_dir)
