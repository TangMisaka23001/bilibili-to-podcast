"""Prune stale collection directories from output/ before each pipeline run.

A "collection directory" is `output/bilibili-{season|series}/{sid}/`. After
loading the active collection list from config (legacy `season`/`series` keys
or the newer `sources:` URLs that get resolved via the URL extractor), any
collection directory whose sid is NOT in that active list is removed.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.tools.extract_url import parse_sources, to_legacy_config


@dataclass
class PruneResult:
    deleted_season: list[str] = field(default_factory=list)
    deleted_series: list[str] = field(default_factory=list)


def _active_sids(config: dict) -> tuple[set[str], set[str]]:
    sources = config.get("sources")
    has_legacy = "season" in config or "series" in config

    if sources is not None and has_legacy:
        raise ValueError(
            "config defines both 'sources' and 'season'/'series'; use one or the other"
        )

    if sources is not None:
        legacy = to_legacy_config(parse_sources(sources))
        season_cfg, series_cfg = legacy["season"], legacy["series"]
    else:
        season_cfg = config.get("season", []) or []
        series_cfg = config.get("series", []) or []

    return {str(c["sid"]) for c in season_cfg}, {str(c["sid"]) for c in series_cfg}


def _scan(output_root: Path, kind: str, keep: set[str]) -> tuple[list[str], list[Path]]:
    base = output_root / f"bilibili-{kind}"
    if not base.is_dir():
        return [], []
    names: list[str] = []
    paths: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name in keep:
            continue
        names.append(entry.name)
        paths.append(entry)
    return names, paths


def prune(config_path: Path | str, output_root: str | Path = "../output/") -> PruneResult:
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    season_sids, series_sids = _active_sids(config)
    root = Path(output_root)

    season_names, season_paths = _scan(root, "season", season_sids)
    series_names, series_paths = _scan(root, "series", series_sids)

    for p in season_paths:
        shutil.rmtree(p)
    for p in series_paths:
        shutil.rmtree(p)

    return PruneResult(deleted_season=season_names, deleted_series=series_names)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="../config.yaml")
    parser.add_argument("--output-root", default="../output/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    season_sids, series_sids = _active_sids(config)
    root = Path(args.output_root)
    season_names, season_paths = _scan(root, "season", season_sids)
    series_names, series_paths = _scan(root, "series", series_sids)

    if args.dry_run:
        print(f"would delete (season): {season_names}")
        print(f"would delete (series): {series_names}")
        return 0

    for p in season_paths:
        shutil.rmtree(p)
    for p in series_paths:
        shutil.rmtree(p)
    print(f"deleted (season): {season_names}")
    print(f"deleted (series): {series_names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
