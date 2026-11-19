"""Audio extraction via yt_dlp + playurl mirror fallback, picture download via requests."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import yt_dlp
from yt_dlp.utils import DownloadError
from bilibili_api import video as video_api

from bilibili_podcast import config as _config
from bilibili_podcast.bilibili.credential import load_credential, materialize_cookie_file
from bilibili_podcast.bilibili.loudness import normalize_loudness
from bilibili_podcast.config import AUDIO_FORMAT, bilibili_link_prefix
from bilibili_podcast.logger import get_logger
from bilibili_podcast.store import StorePath, is_remote, stage_for

if TYPE_CHECKING:
    from bilibili_podcast.bilibili.channel import ChannelRef

logger = get_logger()

#: 本地后端是 ``pathlib.Path``，对象存储后端是 ``S3Path``；两者都能 ``/``、
#: ``write_bytes``，所以同一份代码可以在两种后端上跑。
PathLike = Path | StorePath

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

#: 分块 Range 请求的块大小。B 站的 CDN 边缘节点（Akamai）对**不带 Range
#: 头**的完整 GET 会直接回 403 "Access Denied"，只有带 Range 的部分请求才
#: 放行。实测 `Range: bytes=0-` 在部分节点仍被拒，而**有界**的
#: `Range: bytes=0-1048575` 两个节点都返回 206，所以下载必须分块走。
_RANGE_BLOCK = 1 << 20


def _remove_stale_partials(video_dir: Path, bv: str) -> None:
    """Drop leftover partial downloads for ``bv``.

    A stale ``.part`` from a failed attempt (or from a previous format) would
    otherwise be resumed by yt-dlp and poison the next download. The playurl
    fallback's ``.m4s`` segment counts too: it is only a staging file for the
    remux, so a leftover one is dead weight that would be published as a
    truncated episode next to the real ``.m4a``.
    """
    for pattern in (f"{bv}*.part", f"{bv}*.ytdl", f"{bv}*.m4s"):
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
    """Download ``url`` to ``dest`` using bounded Range requests, verifying MD5.

    Two CDN behaviours shape this function:

    * **Bounded Range is mandatory.** Bilibili's CDN edge answers a plain GET
      with HTTP 403 "Access Denied", no matter how complete the browser
      headers are. Only a request carrying ``Range`` is served, so the body is
      pulled block by block (``_RANGE_BLOCK`` bytes each).
    * **A broken edge may answer 200 with the right Content-Length and then
      close the connection halfway through.** Resuming makes a transient drop
      cheap; the final size + ``X-Upos-Md5`` check rejects a permanently
      truncated object so the caller can move on to the next mirror.

    The server advertises the total size in ``Content-Range`` and the expected
    checksum in ``X-Upos-Md5``, so a partial download is always detectable.
    """
    expected_md5: str | None = None
    expected_size: int | None = None

    for _ in range(_DOWNLOAD_ATTEMPTS):
        pos = dest.stat().st_size if dest.exists() else 0
        while True:
            headers = dict(_CDN_HEADERS)
            headers["Range"] = f"bytes={pos}-{pos + _RANGE_BLOCK - 1}"
            try:
                with requests.get(
                    url, headers=headers, stream=True, timeout=(10, 60)
                ) as response:
                    if response.status_code == 416:
                        break
                    response.raise_for_status()
                    expected_md5 = response.headers.get("X-Upos-Md5") or expected_md5
                    content_range = response.headers.get("Content-Range") or ""
                    if "/" in content_range:
                        total = content_range.rsplit("/", 1)[-1]
                        if total.isdigit():
                            expected_size = int(total)
                    length = int(response.headers.get("Content-Length") or 0)
                    if response.status_code == 200 and pos:
                        # Server ignored Range: restart instead of appending a duplicate.
                        dest.unlink(missing_ok=True)
                        pos = 0
                    if length and expected_size is None:
                        expected_size = pos + length
                    with dest.open("ab") as handle:
                        for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                            if chunk:
                                handle.write(chunk)
            except (requests.RequestException, OSError) as exc:
                written = dest.stat().st_size if dest.exists() else 0
                logger.warning(f"===> download interrupted at {written} bytes: {exc}")
                break

            size = dest.stat().st_size if dest.exists() else 0
            if expected_size is not None and size >= expected_size:
                break
            if size <= pos:
                # No progress (e.g. the edge answered 200 with an empty body):
                # retry the same range instead of spinning forever.
                logger.warning(f"===> download stalled at {size}/{expected_size} bytes")
                break
            pos = size
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


def _normalize_if_enabled(path: Path) -> Path:
    """Normalize a freshly downloaded file when the feature is enabled.

    The switch is read at call time (``config.NORMALIZE_LOUDNESS``) instead of
    at import time so a config reload or an env override takes effect without
    restarting the process.
    """
    if not _config.NORMALIZE_LOUDNESS:
        return path
    try:
        normalize_loudness(path)
    except Exception as exc:  # pragma: no cover - defensive, ffmpeg failures
        logger.warning(f"===> {path.name}: loudness normalization skipped: {exc}")
    return path


def _download_audio_local(ref: "ChannelRef", bv: str, work_dir: Path) -> Path:
    """Download and extract ``bv``'s audio into ``work_dir``; return the .m4a.

    ``work_dir`` is always a real local directory — yt-dlp and ffmpeg cannot
    work on an object-storage key. The caller owns the staging directory and
    uploads the finished file back to the backend (see :func:`download_audio`).
    """
    url = bilibili_link_prefix + bv
    last_error: Exception | None = None
    cookie_file, is_temporary = materialize_cookie_file()

    try:
        for fmt in AUDIO_FORMAT_FALLBACKS:
            _remove_stale_partials(work_dir, bv)
            opts = {
                "format": fmt,
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_FORMAT}
                ],
                "outtmpl": str(work_dir / bv),
                "cookiefile": cookie_file,
                "retries": 10,
                "fragment_retries": 10,
                "file_access_retries": 3,
                # Bilibili's CDN answers a plain GET with 403 "Access Denied".
                # A bounded Range request is served, so force yt-dlp into its
                # chunked mode (``http_chunk_size`` makes it send
                # ``Range: bytes=N-N+chunk``) and hand it the headers the edge
                # expects.
                "http_headers": dict(_CDN_HEADERS),
                "http_chunk_size": _RANGE_BLOCK,
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:  # pyright: ignore[reportArgumentType]
                    ydl.download([url])
                if last_error is not None:
                    logger.warning(f"===> {bv}: succeeded with fallback format {fmt}")
                _remove_stale_partials(work_dir, bv)
                return _normalize_if_enabled(work_dir / f"{bv}.{AUDIO_FORMAT}")
            except DownloadError as e:
                last_error = e
                logger.warning(f"===> {bv}: format {fmt} failed, trying next: {str(e)[:120]}")
    finally:
        if is_temporary and cookie_file:
            Path(cookie_file).unlink(missing_ok=True)

    logger.warning(f"===> {bv}: all yt-dlp formats failed, trying playurl mirrors")
    try:
        _download_via_playurl(bv, work_dir)
    except Exception as fallback_error:
        logger.error(f"===> {bv}: playurl mirror fallback failed: {fallback_error}")
        raise last_error from fallback_error  # type: ignore[misc]
    _remove_stale_partials(work_dir, bv)
    logger.warning(f"===> {bv}: succeeded via playurl mirror")
    return _normalize_if_enabled(work_dir / f"{bv}.{AUDIO_FORMAT}")


def download_audio(ref: "ChannelRef", bv: str, video_dir: PathLike) -> None:
    """Download ``bv``'s audio into ``video_dir``.

    本地后端：直接在 ``video_dir`` 里下载，行为与改造前完全一致。
    对象存储后端：yt-dlp / ffmpeg 必须拿到真实本地文件，所以先在临时工作
    目录里下载与转码，再把成品 .m4a 上传回 ``video_dir``。
    """
    if not is_remote(video_dir):
        _download_audio_local(ref, bv, Path(video_dir))
        return

    with stage_for(video_dir) as stage:
        local = _download_audio_local(ref, bv, stage.path)
        if not local.is_file():
            raise RuntimeError(f"audio download produced no file for {bv}")
        stage.put(local)


def download_picture(pic_url: str, dest: PathLike) -> None:
    """Download a cover image; fails loudly rather than saving an error page."""
    response = requests.get(pic_url, headers=_CDN_HEADERS, timeout=(10, 60))
    response.raise_for_status()
    if not is_remote(dest):
        dest.write_bytes(response.content)
        return
    # 对象存储：先落本地临时文件，再上传到目标 key。
    with stage_for(dest.parent) as stage:
        local = stage.path / dest.name
        local.write_bytes(response.content)
        stage.put_at(dest.name, local)
