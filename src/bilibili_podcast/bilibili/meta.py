"""Read/write channel and video metadata JSON files."""
from pathlib import Path
import json

from bilibili_podcast.config import AUDIO_FORMAT


def write_channel_meta(channel_dir: Path, meta: dict) -> None:
    (channel_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False)
    )


def write_channel_videos(channel_dir: Path, videos: list[dict]) -> None:
    (channel_dir / "videos.json").write_text(
        json.dumps(videos, indent=2, ensure_ascii=False)
    )


def write_video_meta(video_dir: Path, info: dict) -> None:
    (video_dir / "meta.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False)
    )


def write_video_complete(video_dir: Path) -> None:
    (video_dir / "complete").touch()


def video_audio_path(video_dir: Path) -> Path:
    """Where a BV's audio lives — the file the RSS enclosure points at."""
    return video_dir / f"{video_dir.name}.{AUDIO_FORMAT}"


def has_video_audio(video_dir: Path) -> bool:
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


def has_video_complete(channel_dir: Path, bv: str) -> bool:
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
