from pathlib import Path

import pytest
import yaml

from bilibili_podcast.config_loader import load_active_config, ConfigError


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return p


def test_load_active_config_passes_through_legacy_season_and_series(tmp_path):
    cfg = _write(tmp_path, {
        "RSS_URL_PREFIX": "https://x/",
        "PORT": 8000,
        "R2": {"ACCESS_KEY": "a", "SECRET_KEY": "b", "ENDPOINT_URL": "e", "BUCKET_NAME": "bn"},
        "season": [{"uid": "1", "sid": "10"}],
        "series": [{"uid": "2", "sid": "20"}],
    })

    result = load_active_config(cfg)

    assert result["season"] == [{"uid": "1", "sid": "10"}]
    assert result["series"] == [{"uid": "2", "sid": "20"}]
    assert result["RSS_URL_PREFIX"] == "https://x/"


def test_load_active_config_derives_from_sources(tmp_path):
    cfg = _write(tmp_path, {
        "RSS_URL_PREFIX": "https://x/",
        "R2": {"ACCESS_KEY": "a", "SECRET_KEY": "b", "ENDPOINT_URL": "e", "BUCKET_NAME": "bn"},
        "sources": [
            "https://space.bilibili.com/1/lists/10?type=season",
            "https://space.bilibili.com/2/lists/20?type=series",
        ],
    })

    result = load_active_config(cfg)

    assert result["season"] == [{"uid": "1", "sid": "10"}]
    assert result["series"] == [{"uid": "2", "sid": "20"}]


def test_load_active_config_errors_when_sources_and_legacy_both_present(tmp_path):
    cfg = _write(tmp_path, {
        "RSS_URL_PREFIX": "https://x/",
        "R2": {"ACCESS_KEY": "a", "SECRET_KEY": "b", "ENDPOINT_URL": "e", "BUCKET_NAME": "bn"},
        "sources": ["https://space.bilibili.com/1/lists/10?type=season"],
        "season": [{"uid": "9", "sid": "99"}],
    })

    with pytest.raises(ConfigError, match="sources"):
        load_active_config(cfg)


def test_load_active_config_propagates_parse_errors(tmp_path):
    cfg = _write(tmp_path, {
        "R2": {"ACCESS_KEY": "a", "SECRET_KEY": "b", "ENDPOINT_URL": "e", "BUCKET_NAME": "bn"},
        "sources": ["https://example.com/1/lists/10?type=season"],
    })

    with pytest.raises(ConfigError, match="example.com"):
        load_active_config(cfg)


def test_load_active_config_returns_empty_lists_when_no_collections(tmp_path):
    cfg = _write(tmp_path, {
        "RSS_URL_PREFIX": "https://x/",
        "R2": {"ACCESS_KEY": "a", "SECRET_KEY": "b", "ENDPOINT_URL": "e", "BUCKET_NAME": "bn"},
    })

    result = load_active_config(cfg)

    assert result["season"] == []
    assert result["series"] == []
