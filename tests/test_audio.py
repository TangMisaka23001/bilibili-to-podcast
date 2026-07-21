"""Tests for bilibili_podcast.bilibili.audio."""
from __future__ import annotations

from pathlib import Path
import os
from unittest.mock import patch, MagicMock

from bilibili_podcast.bilibili.channel import ChannelRef, ChannelType


def test_download_audio_configures_ytdlp():
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")

    with patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl:
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    opts = mock_ydl.call_args[0][0]
    assert opts["format"] == "worstaudio/worst"
    assert opts["cookiefile"] == "cookie"
    assert opts["outtmpl"] == str(video_dir / "BV1xx")
    assert opts["postprocessors"][0]["preferredcodec"] == "m4a"
    mock_ydl.return_value.__enter__.return_value.download.assert_called_once_with(
        ["https://www.bilibili.com/video/BV1xx"]
    )


def test_download_audio_uses_env_cookie(monkeypatch):
    monkeypatch.setenv("B2P_COOKIE_CONTENT", "# Netscape HTTP Cookie File\nDUMMY=1\n")
    ref = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    video_dir = Path("/tmp/out/10/BV1xx")
    written: list[tuple[str, str]] = []

    def fake_mkstemp(prefix="", suffix=""):
        path = "/tmp/b2p-cookie-fake.txt"
        written.append((path, prefix + suffix))
        return os.open(path, os.O_CREAT | os.O_WRONLY), path

    monkeypatch.setattr("bilibili_podcast.bilibili.audio.tempfile.mkstemp", fake_mkstemp)
    monkeypatch.setattr("bilibili_podcast.bilibili.audio.os.unlink", lambda p: None)

    with patch("bilibili_podcast.bilibili.audio.yt_dlp.YoutubeDL") as mock_ydl:
        from bilibili_podcast.bilibili.audio import download_audio
        download_audio(ref, "BV1xx", video_dir)

    opts = mock_ydl.call_args[0][0]
    cookie_path = opts["cookiefile"]
    assert cookie_path == "/tmp/b2p-cookie-fake.txt"
    assert Path(cookie_path).read_text(encoding="utf-8") == "# Netscape HTTP Cookie File\nDUMMY=1\n"


def test_download_picture_writes_bytes(tmp_path: Path):
    dest = tmp_path / "pic.jpg"
    fake_bytes = b"\xff\xd8\xff\xe0"

    with patch("bilibili_podcast.bilibili.audio.requests.get") as mock_get:
        mock_get.return_value = MagicMock(content=fake_bytes)
        from bilibili_podcast.bilibili.audio import download_picture
        download_picture("https://example.com/pic.jpg", dest)

    assert dest.read_bytes() == fake_bytes
    mock_get.assert_called_once_with("https://example.com/pic.jpg", stream=True)
