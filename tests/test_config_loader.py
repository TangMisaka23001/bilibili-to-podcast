import json
from pathlib import Path

import pytest
import yaml

from bilibili_podcast.config_loader import load_active_config, ConfigError


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return p


def _write_sources(tmp_path: Path, urls: list[str]) -> Path:
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(urls, ensure_ascii=False, indent=2))
    return p


def test_load_active_config_passes_through_legacy_season_and_series(tmp_path):
    _write_yaml(tmp_path, {
        "RSS_URL_PREFIX": "https://x/",
        "PORT": 8000,
    })
    _write_sources(tmp_path, [
        "https://space.bilibili.com/1/lists/10?type=season",
        "https://space.bilibili.com/2/lists/20?type=series",
    ])

    result = load_active_config(tmp_path / "config.yaml")

    assert result["season"] == [{"uid": "1", "sid": "10"}]
    assert result["series"] == [{"uid": "2", "sid": "20"}]
    assert result["RSS_URL_PREFIX"] == "https://x/"


def test_load_active_config_derives_from_sources_json(tmp_path):
    _write_yaml(tmp_path, {"RSS_URL_PREFIX": "https://x/"})
    _write_sources(tmp_path, [
        "https://space.bilibili.com/1/lists/10?type=season",
        "https://space.bilibili.com/2/lists/20?type=series",
    ])

    result = load_active_config(tmp_path / "config.yaml")

    assert result["season"] == [{"uid": "1", "sid": "10"}]
    assert result["series"] == [{"uid": "2", "sid": "20"}]


def test_load_active_config_errors_when_sources_json_missing(tmp_path):
    _write_yaml(tmp_path, {"RSS_URL_PREFIX": "https://x/"})

    with pytest.raises(ConfigError, match="sources.json"):
        load_active_config(tmp_path / "config.yaml")


def test_load_active_config_propagates_parse_errors(tmp_path):
    _write_yaml(tmp_path, {"RSS_URL_PREFIX": "https://x/"})
    _write_sources(tmp_path, ["https://example.com/1/lists/10?type=season"])

    with pytest.raises(ConfigError, match="example.com"):
        load_active_config(tmp_path / "config.yaml")


def test_load_active_config_returns_empty_lists_when_sources_empty(tmp_path):
    _write_yaml(tmp_path, {"RSS_URL_PREFIX": "https://x/"})
    _write_sources(tmp_path, [])

    result = load_active_config(tmp_path / "config.yaml")

    assert result["season"] == []
    assert result["series"] == []


def test_load_active_config_works_when_yaml_missing(tmp_path):
    _write_sources(tmp_path, [
        "https://space.bilibili.com/1/lists/10?type=season",
    ])

    result = load_active_config(tmp_path / "config.yaml")

    assert result["season"] == [{"uid": "1", "sid": "10"}]
    assert result["series"] == []
