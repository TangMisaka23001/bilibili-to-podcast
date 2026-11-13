"""Tests for bilibili_podcast.bilibili.meta — the `complete` sentinel semantics."""
from __future__ import annotations

from pathlib import Path

from bilibili_podcast.bilibili.meta import (
    has_video_audio,
    has_video_complete,
    video_audio_path,
    write_video_complete,
)


def _video_dir(tmp_path: Path, bv: str = "BV1xx") -> Path:
    channel_dir = tmp_path / "100"
    video_dir = channel_dir / bv
    video_dir.mkdir(parents=True)
    return video_dir


def test_video_audio_path_is_bv_named_m4a(tmp_path: Path):
    video_dir = _video_dir(tmp_path)
    assert video_audio_path(video_dir) == video_dir / "BV1xx.m4a"


def test_has_video_audio_false_when_missing(tmp_path: Path):
    assert not has_video_audio(_video_dir(tmp_path))


def test_has_video_audio_false_when_empty(tmp_path: Path):
    """A zero-byte file cannot be played, so it counts as missing."""
    video_dir = _video_dir(tmp_path)
    (video_dir / "BV1xx.m4a").write_bytes(b"")
    assert not has_video_audio(video_dir)


def test_has_video_audio_true_when_present(tmp_path: Path):
    video_dir = _video_dir(tmp_path)
    (video_dir / "BV1xx.m4a").write_bytes(b"audio")
    assert has_video_audio(video_dir)


def test_has_video_complete_requires_the_marker(tmp_path: Path):
    channel_dir = tmp_path / "100"
    video_dir = channel_dir / "BV1xx"
    video_dir.mkdir(parents=True)
    (video_dir / "BV1xx.m4a").write_bytes(b"audio")
    # audio is there but fetch never finished -> not complete
    assert not has_video_complete(channel_dir, "BV1xx")


def test_has_video_complete_false_when_audio_missing(tmp_path: Path):
    """The sentinel can outlive its audio; trusting it would publish a 404."""
    channel_dir = tmp_path / "100"
    video_dir = channel_dir / "BV1xx"
    video_dir.mkdir(parents=True)
    write_video_complete(video_dir)
    assert not has_video_complete(channel_dir, "BV1xx")


def test_has_video_complete_true_when_marker_and_audio_present(tmp_path: Path):
    channel_dir = tmp_path / "100"
    video_dir = channel_dir / "BV1xx"
    video_dir.mkdir(parents=True)
    write_video_complete(video_dir)
    (video_dir / "BV1xx.m4a").write_bytes(b"audio")
    assert has_video_complete(channel_dir, "BV1xx")


def test_has_video_complete_false_for_unknown_bv(tmp_path: Path):
    assert not has_video_complete(tmp_path / "100", "BVmissing")
