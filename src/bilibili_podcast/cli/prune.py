"""Prune stale collection directories from output/ before each pipeline run.

A "collection directory" is `output/bilibili-{season|series}/{sid}/`. After
loading the active collection list from config (via config_loader, which
handles both legacy `season`/`series` keys and the newer `sources:` URLs),
any collection directory whose sid is NOT in that active list is removed.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from bilibili_podcast.config_loader import ConfigError, load_active_config


@dataclass
class PruneResult:
    deleted_season: list[str] = field(default_factory=list)
    deleted_series: list[str] = field(default_factory=list)


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


def _sids(config: dict) -> tuple[set[str], set[str]]:
    return (
        {str(c["sid"]) for c in config.get("season", [])},
        {str(c["sid"]) for c in config.get("series", [])},
    )


def _do_prune(output_root: Path, season_sids: set[str], series_sids: set[str]) -> tuple[list[str], list[str]]:
    season_names, season_paths = _scan(output_root, "season", season_sids)
    series_names, series_paths = _scan(output_root, "series", series_sids)
    for p in season_paths:
        shutil.rmtree(p)
    for p in series_paths:
        shutil.rmtree(p)
    return season_names, series_names


def prune(config_path: Path | str, output_root: str | Path = "output") -> PruneResult:
    config = load_active_config(config_path)
    season_sids, series_sids = _sids(config)
    season_names, series_names = _do_prune(Path(output_root), season_sids, series_sids)
    return PruneResult(deleted_season=season_names, deleted_series=series_names)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="../output/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_active_config(args.config)
    except ConfigError as e:
        print(f"error: {e}")
        return 1

    season_sids, series_sids = _sids(config)
    season_names, season_paths = _scan(Path(args.output_root), "season", season_sids)
    series_names, series_paths = _scan(Path(args.output_root), "series", series_sids)

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
