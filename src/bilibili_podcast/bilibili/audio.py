"""Audio extraction via yt_dlp + picture download via requests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yt_dlp

from bilibili_podcast.config import AUDIO_FORMAT, COOKIE_ENV_VAR, bilibili_link_prefix

if TYPE_CHECKING:
    from bilibili_podcast.bilibili.channel import ChannelRef


def _resolve_cookie_file() -> str | None:
    content = os.environ.get(COOKIE_ENV_VAR)
    if content:
        fd, path = tempfile.mkstemp(prefix="b2p-cookie-", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            os.unlink(path)
            raise
        return path
    return "cookie"


def download_audio(ref: "ChannelRef", bv: str, video_dir: Path) -> None:
    url = bilibili_link_prefix + bv
    cookie_file = _resolve_cookie_file()
    opts = {
        "format": "worstaudio/worst",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT}
        ],
        "outtmpl": str(video_dir / bv),
        "cookiefile": cookie_file,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    finally:
        if cookie_file != "cookie":
            Path(cookie_file).unlink(missing_ok=True)


def download_picture(pic_url: str, dest: Path) -> None:
    response = requests.get(pic_url, stream=True)
    dest.write_bytes(response.content)
