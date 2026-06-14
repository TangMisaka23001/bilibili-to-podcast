import io
import sys
from pathlib import Path

import pytest
import yaml

from bilibili_podcast.cli._config_cli import main


def _run_cli(config: dict, argv: list[str] | None = None):
    if argv is None:
        argv = ["config.yaml"]
    config_path = Path(argv[0])
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))

    out = io.StringIO()
    err = io.StringIO()
    exit_code = main(argv, stdout=out, stderr=err)
    return exit_code, out.getvalue(), err.getvalue(), config_path


def test_cli_passes_through_when_no_sources():
    config = {
        "RSS_URL_PREFIX": "https://podcast.example.com/",
        "season": [{"uid": "1", "sid": "10"}],
        "series": [{"uid": "2", "sid": "20"}],
    }

    exit_code, out, err, path = _run_cli(config)

    assert exit_code == 0
    loaded = yaml.safe_load(out)
    assert loaded["season"] == [{"uid": "1", "sid": "10"}]
    assert loaded["series"] == [{"uid": "2", "sid": "20"}]


def test_cli_derives_legacy_from_sources():
    config = {
        "RSS_URL_PREFIX": "https://podcast.example.com/",
        "sources": [
            "https://space.bilibili.com/1/lists/10?type=season",
            "https://space.bilibili.com/2/lists/20?type=series",
        ],
    }

    exit_code, out, err, path = _run_cli(config)

    assert exit_code == 0
    loaded = yaml.safe_load(out)
    assert loaded["season"] == [{"uid": "1", "sid": "10"}]
    assert loaded["series"] == [{"uid": "2", "sid": "20"}]


def test_cli_errors_when_sources_and_legacy_both_present():
    config = {
        "RSS_URL_PREFIX": "https://podcast.example.com/",
        "sources": ["https://space.bilibili.com/1/lists/10?type=season"],
        "season": [{"uid": "9", "sid": "99"}],
    }

    exit_code, out, err, path = _run_cli(config)

    assert exit_code == 4
    assert "sources" in err and "season" in err


def test_cli_errors_on_invalid_url_in_sources():
    config = {
        "RSS_URL_PREFIX": "https://podcast.example.com/",
        "sources": [
            "https://space.bilibili.com/1/lists/10?type=season",
            "https://example.com/2/lists/20?type=series",
        ],
    }

    exit_code, out, err, path = _run_cli(config)

    assert exit_code == 1
    assert "example.com" in err
