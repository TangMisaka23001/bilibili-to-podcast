"""Read config.yaml (optional) and sources.json, resolve the active config.

`sources.json` (sibling of `config.yaml`) holds the list of B站 collection
URLs to track. Top-level structure is an object with two arrays:

    {
        "all": [ "<url>", ... ],   // long-tracked collections
        "new": [ "<url>", ... ]    // newcomer / pending review
    }

Both lists are concatenated in order (`all` first, then `new`) and the
combined URLs are parsed into uid/sid/type, then bucketed into the
`season` and `series` keys of the returned config dict. The yaml file is
optional: when missing, the result is populated from environment variables
and defaults only. All other top-level yaml fields are passed through unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Union

import yaml

from bilibili_podcast.extract_url import parse_sources, to_legacy_config


class ConfigError(Exception):
    pass


def _load_sources(sources_path: Path) -> tuple[list[str], list[str]]:
    """Read sources.json. Returns (all_urls, new_urls)."""
    try:
        with sources_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"sources file not found: {sources_path}") from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"sources file must be an object with 'all' and 'new' arrays: {sources_path}"
        )
    if "all" not in data and "new" not in data:
        raise ConfigError(
            f"sources file must contain at least one of 'all' / 'new' keys: {sources_path}"
        )

    all_urls: list[str] = []
    new_urls: list[str] = []
    for key, target in (("all", all_urls), ("new", new_urls)):
        value = data.get(key, [])
        if not isinstance(value, list):
            raise ConfigError(
                f"sources file '{key}' must be an array: {sources_path}"
            )
        target.extend(value)

    return all_urls, new_urls


def load_active_config(config_path: Union[str, Path]) -> dict:
    config_path = Path(config_path)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    sources_path = config_path.parent / "sources.json"
    all_urls, new_urls = _load_sources(sources_path)

    try:
        parsed_all = parse_sources(all_urls)
        parsed_new = parse_sources(new_urls)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    config["season"] = to_legacy_config(parsed_all)["season"]
    config["series"] = to_legacy_config(parsed_all)["series"]
    config["new"] = [{"uid": s.uid, "sid": s.sid, "type": s.type} for s in parsed_new]

    return config
