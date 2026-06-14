"""Tests for bilibili_podcast.cli.prune — main() and _scan paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bilibili_podcast.config_loader import ConfigError
from bilibili_podcast.cli.prune import main, _scan


def test_scan_returns_stale_sids(tmp_path: Path):
    (tmp_path / "bilibili-season" / "10").mkdir(parents=True)
    (tmp_path / "bilibili-season" / "20").mkdir()
    (tmp_path / "bilibili-season" / "not-a-dir").touch()

    names, paths = _scan(tmp_path, "season", {"10"})

    assert names == ["20"]
    assert len(paths) == 1
    assert paths[0] == tmp_path / "bilibili-season" / "20"


def test_scan_empty_dir(tmp_path: Path):
    names, paths = _scan(tmp_path, "season", set())
    assert names == []


def test_main_dry_run_prints_and_does_not_delete(tmp_path: Path, monkeypatch):
    (tmp_path / "bilibili-season" / "999").mkdir(parents=True)
    monkeypatch.setattr("bilibili_podcast.cli.prune.load_active_config", lambda p: {
        "season": [{"uid": "1", "sid": "10", "type": "season"}],
        "series": [],
    })

    with patch("builtins.print") as mock_print:
        exit_code = main(["--config", "dummy.yaml", "--output-root", str(tmp_path), "--dry-run"])

    assert exit_code == 0
    assert (tmp_path / "bilibili-season" / "999").exists()
    printed = "".join(str(c) for c in mock_print.call_args_list)
    assert "999" in printed


def test_main_with_config_error_returns_1(monkeypatch):
    monkeypatch.setattr("bilibili_podcast.cli.prune.load_active_config",
                        MagicMock(side_effect=ConfigError("bad")))

    with patch("builtins.print"):
        exit_code = main(["--config", "dummy.yaml"])

    assert exit_code == 1


def test_main_prunes_and_prints(tmp_path: Path, monkeypatch):
    (tmp_path / "bilibili-season" / "10").mkdir(parents=True)
    (tmp_path / "bilibili-season" / "999").mkdir(parents=True)
    monkeypatch.setattr("bilibili_podcast.cli.prune.load_active_config", lambda p: {
        "season": [{"uid": "1", "sid": "10", "type": "season"}],
        "series": [],
    })

    with patch("builtins.print"):
        exit_code = main(["--config", "dummy.yaml", "--output-root", str(tmp_path)])

    assert exit_code == 0
    assert not (tmp_path / "bilibili-season" / "999").exists()
    assert (tmp_path / "bilibili-season" / "10").exists()
