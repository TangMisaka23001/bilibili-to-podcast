"""Read config.yaml (optional) and sources.json, resolve the active config.

`sources.json` (sibling of `config.yaml`) holds the list of B站 collection
URLs to track. Each URL is parsed into uid/sid/type, then bucketed into the
`season` and `series` keys of the returned config dict. The yaml file is
optional: when missing, the result is populated from environment variables
and defaults only.

All other top-level yaml fields are passed through unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml

from bilibili_podcast.extract_url import parse_sources, to_legacy_config


class ConfigError(Exception):
    pass


def load_active_config(config_path: Union[str, Path]) -> dict:
    config_path = Path(config_path)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    sources_path = config_path.parent / "sources.json"
    try:
        with sources_path.open("r", encoding="utf-8") as f:
            urls = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"sources file not found: {sources_path}") from e

    try:
        parsed = parse_sources(urls)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    config["season"] = to_legacy_config(parsed)["season"]
    config["series"] = to_legacy_config(parsed)["series"]

    return config
