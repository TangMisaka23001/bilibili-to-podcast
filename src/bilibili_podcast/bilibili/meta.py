"""Read/write channel and video metadata JSON files."""
from pathlib import Path
import json


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


def has_video_complete(channel_dir: Path, bv: str) -> bool:
    return (channel_dir / bv / "complete").exists()
