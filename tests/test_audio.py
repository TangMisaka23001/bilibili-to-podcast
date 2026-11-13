"""Tests for bilibili_podcast.bilibili.audio."""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
from unittest.mock import patch, MagicMock

import pytest
import requests
import yt_dlp
from yt_dlp.utils import DownloadError

from bilibili_podcast.bilibili.channel import ChannelRef, ChannelType


def test_download_audio_configures_ytdlp():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=("/tmp/b2p-ck.txt", False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl,
    ):
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    opts = mock_ydl.call_args[0][0]
    assert opts["format"] == "worstaudio/worst"
    assert opts["cookiefile"] == "/tmp/b2p-ck.txt"
    assert opts["outtmpl"] == str(video_dir / "BV1xx")
    assert opts["postprocessors"][0]["preferredcodec"] == "m4a"
    assert opts["retries"] > 0
    mock_ydl.return_value.__enter__.return_value.download.assert_called_once_with(
        ["https://www.bilibili.com/video/BV1xx"]
    )


def test_download_audio_omits_cookiefile_when_no_cookie():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl,
    ):
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    assert mock_ydl.call_args[0][0]["cookiefile"] is None


def test_download_audio_uses_env_cookie(monkeypatch):
    monkeypatch.setenv("B2P_COOKIE_CONTENT", "# Netscape HTTP Cookie File\nDUMMY=1\n")
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")
    cookie_path = "/tmp/b2p-cookie-fake.txt"

    def fake_mkstemp(prefix="", suffix=""):
        return os.open(cookie_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC), cookie_path

    monkeypatch.setattr(
        "bilibili_podcast.bilibili.credential.tempfile.mkstemp", fake_mkstemp
    )

    # yt-dlp is mocked, so read the temp cookie at download time — it is
    # cleaned up by download_audio's finally block afterwards.
    observed: dict[str, str] = {}

    with patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl:
        def capture(urls):
            opts = mock_ydl.call_args[0][0]
            observed["cookie"] = Path(opts["cookiefile"]).read_text(encoding="utf-8")

        mock_ydl.return_value.__enter__.return_value.download.side_effect = capture
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    assert mock_ydl.call_args[0][0]["cookiefile"] == cookie_path
    assert observed["cookie"] == "# Netscape HTTP Cookie File\nDUMMY=1\n"
    # temp cookie must not survive the download
    assert not Path(cookie_path).exists()


def test_download_audio_removes_stale_partials(tmp_path: Path):
    video_dir = tmp_path / "BV1"
    video_dir.mkdir()
    stale = [
        video_dir / "BV1.part",
        video_dir / "BV1.f30216.part",
        video_dir / "BV1.ytdl",
        video_dir / "BV1.30216.m4s",
    ]
    for path in stale:
        path.write_bytes(b"partial")
    unrelated = video_dir / "BV2.part"
    unrelated.write_bytes(b"other")

    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL"),
    ):
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1", video_dir)

    assert not any(p.exists() for p in stale)
    assert unrelated.exists()


def test_download_audio_falls_back_to_next_format():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")
    attempted: list[str] = []

    def fake_ydl(opts):
        attempted.append(opts["format"])
        handle = MagicMock()
        if opts["format"] == "worstaudio/worst":
            handle.__enter__.return_value.download.side_effect = DownloadError(
                "HTTP Error 503"
            )
        return handle

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL", side_effect=fake_ydl),
    ):
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    assert attempted == ["worstaudio/worst", "worstaudio[abr>=70]/bestaudio"]


def test_download_audio_walks_all_fallbacks():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")
    attempted: list[str] = []

    def fake_ydl(opts):
        attempted.append(opts["format"])
        handle = MagicMock()
        if opts["format"] != "bestaudio/best":
            handle.__enter__.return_value.download.side_effect = DownloadError(
                "HTTP Error 503"
            )
        return handle

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL", side_effect=fake_ydl),
    ):
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    assert attempted == [
        "worstaudio/worst",
        "worstaudio[abr>=70]/bestaudio",
        "bestaudio/best",
    ]


def test_download_audio_raises_when_all_formats_fail():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl,
        patch(
            "bilibili_podcast.bilibili.audio._download_via_playurl",
            side_effect=RuntimeError("no mirror"),
        ) as mock_fallback,
    ):
        mock_ydl.return_value.__enter__.return_value.download.side_effect = (
            DownloadError("boom")
        )
        from bilibili_podcast.bilibili.audio import download_audio
        with pytest.raises(DownloadError):
            download_audio(ref, "BV1xx", video_dir)

    assert mock_ydl.call_count == 3
    mock_fallback.assert_called_once_with("BV1xx", video_dir)


def test_download_audio_uses_playurl_mirror_after_ytdlp_fails():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")

    with (
        patch(
            "bilibili_podcast.bilibili.audio.materialize_cookie_file",
            return_value=(None, False),
        ),
        patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl,
        patch(
            "bilibili_podcast.bilibili.audio._download_via_playurl"
        ) as mock_fallback,
    ):
        mock_ydl.return_value.__enter__.return_value.download.side_effect = (
            DownloadError("truncated")
        )
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    mock_fallback.assert_called_once_with("BV1xx", video_dir)


def test_download_via_playurl_prefers_official_backup():
    streams = [{
        "id": 30216,
        "bandwidth": 66155,
        "baseUrl": "https://primary.example/a.m4s",
        "backupUrl": ["https://backup.example/a.m4s"],
    }]
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> None:
        calls.append(url)

    from bilibili_podcast.bilibili.audio import _download_via_playurl
    with (
        patch("bilibili_podcast.bilibili.audio._audio_streams", return_value=streams),
        patch("bilibili_podcast.bilibili.audio._download_stream", side_effect=fake_download),
        patch("bilibili_podcast.bilibili.audio._remux_to_audio"),
    ):
        _download_via_playurl("BV1xx", Path("/tmp/out/10/BV1xx"))

    assert calls == ["https://backup.example/a.m4s"]


def test_download_stream_resumes_with_range_and_checks_md5(tmp_path: Path):
    payload = b"0123456789"
    digest = hashlib.md5(payload).hexdigest()
    dest = tmp_path / "a.m4s"

    class FakeResponse:
        def __init__(self, status, headers, chunks, error=None):
            self.status_code = status
            self.headers = headers
            self._chunks = chunks
            self._error = error

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, size):
            yield from self._chunks
            if self._error:
                raise self._error

    responses = [
        FakeResponse(
            200,
            {"Content-Length": str(len(payload)), "X-Upos-Md5": digest},
            [payload[:4]],
            requests.exceptions.ChunkedEncodingError("broken"),
        ),
        FakeResponse(
            206,
            {"Content-Length": str(len(payload) - 4), "X-Upos-Md5": digest},
            [payload[4:]],
        ),
    ]
    seen_headers: list[dict] = []

    def fake_get(url, headers, stream, timeout):
        seen_headers.append(headers)
        return responses.pop(0)

    from bilibili_podcast.bilibili.audio import _download_stream
    with patch("bilibili_podcast.bilibili.audio.requests.get", side_effect=fake_get):
        _download_stream("https://backup.example/a.m4s", dest)

    assert dest.read_bytes() == payload
    assert "Range" not in seen_headers[0]
    assert seen_headers[1]["Range"] == "bytes=4-"


def test_download_picture_writes_bytes(tmp_path: Path):
    dest = tmp_path / "pic.jpg"
    fake_bytes = b"\xff\xd8\xff\xe0"

    with patch("bilibili_podcast.bilibili.audio.requests.get") as mock_get:
        mock_get.return_value = MagicMock(content=fake_bytes)
        from bilibili_podcast.bilibili.audio import download_picture
        download_picture("https://example.com/pic.jpg", dest)

    assert dest.read_bytes() == fake_bytes
    # 必须带超时，否则 requests 会无限期挂起
    assert mock_get.call_args.kwargs["timeout"] == (10, 60)


def test_download_picture_refuses_error_page(tmp_path: Path):
    """403/412 的 HTML 错误页不能被当成 jpg 落盘。"""
    dest = tmp_path / "pic.jpg"

    with patch("bilibili_podcast.bilibili.audio.requests.get") as mock_get:
        response = MagicMock(content=b"<html>403</html>")
        response.raise_for_status.side_effect = requests.HTTPError("403")
        mock_get.return_value = response
        from bilibili_podcast.bilibili.audio import download_picture
        with pytest.raises(requests.HTTPError):
            download_picture("https://example.com/pic.jpg", dest)

    assert not dest.exists()
