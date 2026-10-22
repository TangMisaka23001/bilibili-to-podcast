"""Audio extraction via yt_dlp + playurl mirror fallback, picture download via requests."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yt_dlp
from bilibili_api import video as video_api

from bilibili_podcast.bilibili.credential import load_credential, materialize_cookie_file
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

#: Bilibili's CDN rejects direct playurl requests without these headers.
_CDN_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_DOWNLOAD_CHUNK = 1 << 16
_DOWNLOAD_ATTEMPTS = 5


def _remove_stale_partials(video_dir: Path, bv: str) -> None:
    """Drop leftover partial downloads for ``bv``.

    A stale ``.part`` from a failed attempt (or from a previous format) would
    otherwise be resumed by yt-dlp and poison the next download.
    """
    for pattern in (f"{bv}*.part", f"{bv}*.ytdl"):
        for path in video_dir.glob(pattern):
            logger.info(f"===> removing stale partial {path}")
            path.unlink(missing_ok=True)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_stream(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest``, resuming with Range and verifying MD5.

    A broken CDN edge may answer HTTP 200 with the right Content-Length and then
    close the connection halfway through. Resuming makes a transient drop cheap;
    the final size + ``X-Upos-Md5`` check rejects a permanently truncated object
    so the caller can move on to the next mirror.
    """
    expected_md5: str | None = None
    expected_size: int | None = None

    for _ in range(_DOWNLOAD_ATTEMPTS):
        pos = dest.stat().st_size if dest.exists() else 0
        headers = dict(_CDN_HEADERS)
        if pos:
            headers["Range"] = f"bytes={pos}-"
        try:
            with requests.get(
                url, headers=headers, stream=True, timeout=(10, 60)
            ) as response:
                if response.status_code == 416:
                    break
                response.raise_for_status()
                expected_md5 = response.headers.get("X-Upos-Md5") or expected_md5
                length = int(response.headers.get("Content-Length") or 0)
                if response.status_code == 200 and pos:
                    # Server ignored Range: restart instead of appending a duplicate.
                    dest.unlink(missing_ok=True)
                    pos = 0
                if length:
                    expected_size = pos + length
                with dest.open("ab") as handle:
                    for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                        if chunk:
                            handle.write(chunk)
        except (requests.RequestException, OSError) as exc:
            written = dest.stat().st_size if dest.exists() else 0
            logger.warning(f"===> download interrupted at {written} bytes: {exc}")
            continue

        size = dest.stat().st_size if dest.exists() else 0
        if expected_size is None or size >= expected_size:
            break
        logger.warning(f"===> download ended early: {size}/{expected_size} bytes")
    else:
        raise RuntimeError(f"download did not complete after {_DOWNLOAD_ATTEMPTS} attempts")

    size = dest.stat().st_size if dest.exists() else 0
    if expected_size is not None and size != expected_size:
        raise RuntimeError(f"incomplete download: {size}/{expected_size} bytes")
    if expected_md5:
        actual = _md5(dest)
        if actual.lower() != expected_md5.lower():
            raise RuntimeError(f"MD5 mismatch: {actual} != {expected_md5}")


def _remux_to_audio(src: Path, dest: Path) -> None:
    """Remux the fMP4 audio stream into ``dest`` without re-encoding."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src), "-vn", "-c:a", "copy", str(dest),
        ],
        check=True,
    )
    src.unlink(missing_ok=True)


def _audio_streams(bv: str) -> list[dict]:
    """Return playurl DASH audio streams, smallest (cheapest) first."""
    video = video_api.Video(bvid=bv, credential=load_credential())
    data = asyncio.run(video.get_download_url(page_index=0))
    streams = (data.get("dash") or {}).get("audio") or []
    return sorted(streams, key=lambda stream: stream.get("bandwidth") or 0)


def _download_via_playurl(bv: str, video_dir: Path) -> None:
    """Fallback: download DASH audio from playurl's official backup mirrors.

    yt-dlp's Bilibili extractor only reads ``baseUrl`` and ignores
    ``backupUrl``. When the primary edge serves a truncated object, every
    yt-dlp format fails even though the same object is intact on the backup
    host. We reach this function only after ``baseUrl`` already failed, so try
    the backups first.
    """
    streams = _audio_streams(bv)
    if not streams:
        raise RuntimeError(f"playurl has no DASH audio streams for {bv}")

    last_error: Exception | None = None
    for stream in streams:
        stream_id = stream.get("id") or "audio"
        urls = [
            url
            for url in [*(stream.get("backupUrl") or []), stream.get("baseUrl")]
            if url
        ]
        for url in urls:
            part = video_dir / f"{bv}.{stream_id}.m4s"
            part.unlink(missing_ok=True)
            host = url.split("/")[2]
            try:
                _download_stream(url, part)
                _remux_to_audio(part, video_dir / f"{bv}.{AUDIO_FORMAT}")
                logger.warning(f"===> {bv}: downloaded {stream_id} from {host}")
                return
            except Exception as exc:
                last_error = exc
                part.unlink(missing_ok=True)
                logger.warning(f"===> {bv}: {stream_id} from {host} failed: {exc}")
    raise RuntimeError(f"no playurl mirror delivered {bv}") from last_error


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
            _remove_stale_partials(video_dir, bv)
            return
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            logger.warning(f"===> {bv}: format {fmt} failed, trying next: {str(e)[:120]}")
        finally:
            if is_temporary and cookie_file:
                Path(cookie_file).unlink(missing_ok=True)

    logger.warning(f"===> {bv}: all yt-dlp formats failed, trying playurl mirrors")
    try:
        _download_via_playurl(bv, video_dir)
    except Exception as fallback_error:
        logger.error(f"===> {bv}: playurl mirror fallback failed: {fallback_error}")
        raise last_error from fallback_error  # type: ignore[misc]
    _remove_stale_partials(video_dir, bv)
    logger.warning(f"===> {bv}: succeeded via playurl mirror")


def download_picture(pic_url: str, dest: Path) -> None:
    response = requests.get(pic_url, stream=True)
    dest.write_bytes(response.content)
