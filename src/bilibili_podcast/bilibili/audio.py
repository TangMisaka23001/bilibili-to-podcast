"""Audio extraction via yt_dlp + picture download via requests."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yt_dlp

from bilibili_podcast.bilibili.credential import materialize_cookie_file
from bilibili_podcast.config import AUDIO_FORMAT, bilibili_link_prefix
from bilibili_podcast.logger import get_logger

if TYPE_CHECKING:
    from bilibili_podcast.bilibili.channel import ChannelRef

logger = get_logger()

#: yt-dlp format selectors, smallest (cheapest) first. A CDN object can contain a
#: corrupt byte range that answers HTTP 503 forever, which no retry can get past —
#: falling back to the next audio stream keeps the episode instead of dropping it.
AUDIO_FORMAT_FALLBACKS = (
    "worstaudio/worst",                # ~66k
    "worstaudio[abr>=70]/bestaudio",   # ~84k
    "bestaudio/best",                  # ~147k
)


def _remove_stale_partials(video_dir: Path, bv: str) -> None:
    """Drop leftover partial downloads for ``bv``.

    A stale ``.part`` from a failed attempt (or from a previous format) would
    otherwise be resumed by yt-dlp and poison the next download.
    """
    for pattern in (f"{bv}*.part", f"{bv}*.ytdl"):
        for path in video_dir.glob(pattern):
            logger.info(f"===> removing stale partial {path}")
            path.unlink(missing_ok=True)


def download_audio(ref: "ChannelRef", bv: str, video_dir: Path) -> None:
    url = bilibili_link_prefix + bv
    last_error: Exception | None = None

    for fmt in AUDIO_FORMAT_FALLBACKS:
        _remove_stale_partials(video_dir, bv)
        cookie_file, is_temporary = materialize_cookie_file()
        opts = {
            "format": fmt,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT}
            ],
            "outtmpl": str(video_dir / bv),
            "cookiefile": cookie_file,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 3,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            if last_error is not None:
                logger.warning(f"===> {bv}: succeeded with fallback format {fmt}")
            return
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            logger.warning(f"===> {bv}: format {fmt} failed, trying next: {str(e)[:120]}")
        finally:
            if is_temporary and cookie_file:
                Path(cookie_file).unlink(missing_ok=True)

    raise last_error  # type: ignore[misc]


def download_picture(pic_url: str, dest: Path) -> None:
    response = requests.get(pic_url, stream=True)
    dest.write_bytes(response.content)
