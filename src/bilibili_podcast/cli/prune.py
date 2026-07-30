"""Prune stale collection directories from output/ before each pipeline run.

A "collection directory" is `output/bilibili-{season|series|new}/{sid}/`. After
loading the active collection list from config (via config_loader, which
handles both legacy `season`/`series` keys and the newer `sources:` URLs
and the new `{all, new}` object), any collection directory whose sid is NOT
in that active list is removed. This keeps local output in sync with
`sources.json`: dropping a URL from any of the three arrays removes the
corresponding output on the next prune run.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from bilibili_podcast.config_loader import ConfigError, load_active_config


@dataclass
class PruneResult:
    deleted_season: list[str] = field(default_factory=list)
    deleted_series: list[str] = field(default_factory=list)
    deleted_new: list[str] = field(default_factory=list)


_KINDS = ("season", "series", "new")


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


def _sids(config: dict) -> dict[str, set[str]]:
    return {
        "season": {str(c["sid"]) for c in config.get("season", [])},
        "series": {str(c["sid"]) for c in config.get("series", [])},
        "new": {str(c["sid"]) for c in config.get("new", [])},
    }


def _do_prune(output_root: Path, sids_by_kind: dict[str, set[str]]) -> dict[str, list[str]]:
    deleted: dict[str, list[str]] = {}
    for kind in _KINDS:
        names, paths = _scan(output_root, kind, sids_by_kind[kind])
        for p in paths:
            shutil.rmtree(p)
        deleted[kind] = names
    return deleted


def prune(config_path: Path | str, output_root: str | Path = "output") -> PruneResult:
    config = load_active_config(config_path)
    sids_by_kind = _sids(config)
    deleted = _do_prune(Path(output_root), sids_by_kind)
    return PruneResult(
        deleted_season=deleted["season"],
        deleted_series=deleted["series"],
        deleted_new=deleted["new"],
    )


def _format_dry_run(kind: str, names: Iterable[str]) -> str:
    return f"would delete ({kind}): {sorted(names)}"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_active_config(args.config)
    except ConfigError as e:
        print(f"error: {e}")
        return 1

    sids_by_kind = _sids(config)
    deleted_paths: dict[str, list[Path]] = {}
    deleted_names: dict[str, list[str]] = {}
    for kind in _KINDS:
        names, paths = _scan(Path(args.output_root), kind, sids_by_kind[kind])
        deleted_names[kind] = names
        deleted_paths[kind] = paths

    if args.dry_run:
        for kind in _KINDS:
            print(_format_dry_run(kind, deleted_names[kind]))
        return 0

    for kind in _KINDS:
        for p in deleted_paths[kind]:
            shutil.rmtree(p)
        print(f"deleted ({kind}): {deleted_names[kind]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())