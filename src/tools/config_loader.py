"""Read config.yaml and resolve the active collection list.

Supports two equivalent config shapes:
1. Legacy: explicit `season:` and `series:` arrays of `{uid, sid}`.
2. New:    a `sources:` array of full B站 collection URLs.

The function returns a dict that always contains both `season` and `series`
keys (possibly empty), plus all the other top-level yaml fields unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml

from src.tools.extract_url import parse_sources, to_legacy_config


class ConfigError(Exception):
    pass


def load_active_config(config_path: Union[str, Path]) -> dict:
    config_path = Path(config_path)
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {config_path}") from e

    sources = config.get("sources")
    has_legacy = "season" in config or "series" in config

    if sources is not None and has_legacy:
        raise ConfigError(
            "config defines both 'sources' and 'season'/'series'; use one or the other"
        )

    if sources is not None:
        try:
            parsed = parse_sources(sources)
        except ValueError as e:
            raise ConfigError(str(e)) from e
        config["season"] = to_legacy_config(parsed)["season"]
        config["series"] = to_legacy_config(parsed)["series"]
    else:
        config.setdefault("season", [])
        config.setdefault("series", [])

    return config
