"""Tests for bilibili_podcast.cli.size."""
from __future__ import annotations

import io
import contextlib
from pathlib import Path

from bilibili_podcast.cli.size import _humanize, report


def test_humanize_bytes():
    assert _humanize(0) == "0B"
    assert _humanize(512) == "512B"
    assert _humanize(1024) == "1.0K"
    assert _humanize(1536) == "1.5K"
    assert _humanize(1024 * 1024) == "1.0M"
    assert _humanize(int(1024 ** 3 * 1.5)) == "1.5G"
    assert _humanize(1024 ** 4) == "1.0T"


def test_report_prints_total_when_output_exists(tmp_path: Path):
    (tmp_path / "bilibili-season" / "100").mkdir(parents=True)
    (tmp_path / "bilibili-season" / "100" / "audio.m4a").write_bytes(b"x" * 2048)
    (tmp_path / "rss").mkdir()
    (tmp_path / "rss" / "100.xml").write_bytes(b"y" * 512)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(tmp_path)

    out = buf.getvalue()
    assert f"{tmp_path}/  total:" in out
    assert "bilibili-season/: 2.0K" in out
    assert "rss/: 512B" in out
    # Missing subdirs are omitted, not errored
    assert "bilibili-series" not in out
    assert "bilibili-new" not in out


def test_report_handles_missing_output_root(tmp_path: Path):
    missing = tmp_path / "does-not-exist"

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(missing)

    assert "not found" in buf.getvalue()


def test_report_walks_nested_directories(tmp_path: Path):
    """Sub-subdirectories are walked recursively (e.g. bilibili-season/{sid}/{bvid}/)."""
    base = tmp_path / "bilibili-season" / "100" / "BV1"
    base.mkdir(parents=True)
    (base / "audio.m4a").write_bytes(b"a" * 100)
    (base / "meta.json").write_bytes(b"b" * 200)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        report(tmp_path)

    assert "300B" in buf.getvalue()