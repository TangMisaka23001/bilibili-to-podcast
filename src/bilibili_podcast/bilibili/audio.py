"""Audio extraction via yt_dlp + picture download via requests."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yt_dlp

from bilibili_podcast.config import AUDIO_FORMAT, bilibili_link_prefix

if TYPE_CHECKING:
    from bilibili_podcast.bilibili.channel import ChannelRef


def download_audio(ref: "ChannelRef", bv: str, video_dir: Path) -> None:
    url = bilibili_link_prefix + bv
    opts = {
        "format": "worstaudio/worst",
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT}
        ],
        "outtmpl": str(video_dir / bv),
        "cookiefile": "cookie",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def download_picture(pic_url: str, dest: Path) -> None:
    response = requests.get(pic_url, stream=True)
    dest.write_bytes(response.content)
