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

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bilibili_podcast.config_loader import (
    ConfigError,
    add_backend_argument,
    load_active_config,
)
from bilibili_podcast.logger import get_logger
from bilibili_podcast.store import remove_tree, resolve_root

logger = get_logger()


@dataclass
class PruneResult:
    deleted_season: list[str] = field(default_factory=list)
    deleted_series: list[str] = field(default_factory=list)
    deleted_new: list[str] = field(default_factory=list)


_KINDS = ("season", "series", "new")


def _scan(output_root: Any, kind: str, keep: set[str]) -> tuple[list[str], list[Path]]:
    """列出 ``bilibili-{kind}/`` 下不在 keep 里的子目录。

    本地下返回真实 ``pathlib.Path``（调用方按路径删除）；远端下返回
    ``S3Path``，删除走 ``remove_tree`` 按前缀删。
    """
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


def _feed_path(output_root: Any, kind: str, sid: str) -> Any:
    """合集对应的 feed 文件：``rss/{kind}/{sid}.xml``。

    feed 只能由它对应的合集目录重新生成；合集被 prune 掉之后，这份 feed
    里的每一个 episode 都会指向 404，所以必须跟着一起删。
    """
    return output_root / "rss" / kind / f"{sid}.xml"


def _scan_orphan_feeds(output_root: Any, kind: str, keep: set[str]) -> list[Any]:
    """列出 ``rss/{kind}/`` 下 sid 不在 keep 里的孤儿 feed。

    合集目录可能早在上一轮 prune 就被删了，但 feed 留了下来——那份 feed 里
    的每个 episode 都指向已经不存在的音频，订阅者看到的是一堆 404。config
    是「发布了哪些合集」的真相源，所以 sid 不在 keep 里的 feed 都算孤儿。
    """
    base = output_root / "rss" / kind
    if not base.is_dir():
        return []
    orphans: list[Any] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_file() or not entry.name.endswith(".xml"):
            continue
        if entry.stem in keep:
            continue
        orphans.append(entry)
    return orphans


def _prune_orphan_feeds(output_root: Any, sids_by_kind: dict[str, set[str]]) -> dict[str, list[str]]:
    """清理孤儿 feed，返回 ``{kind: [被删的 sid]}``。"""
    removed: dict[str, list[str]] = {}
    for kind in ("season", "series"):
        orphans = _scan_orphan_feeds(output_root, kind, sids_by_kind[kind])
        removed[kind] = []
        for feed in orphans:
            try:
                feed.unlink()
            except Exception as e:  # noqa: BLE001 - 单个 feed 删不掉不该中断整轮
                logger.warning(f"===> failed to prune feed {feed}: {e}")
                continue
            removed[kind].append(feed.stem)
            logger.info(f"===> pruned orphan feed {feed}")
    return removed


def _do_prune(output_root: Any, sids_by_kind: dict[str, set[str]]) -> dict[str, list[str]]:
    deleted: dict[str, list[str]] = {}
    for kind in _KINDS:
        names, paths = _scan(output_root, kind, sids_by_kind[kind])
        for p in paths:
            try:
                remove_tree(p)
            except Exception as e:  # noqa: BLE001 - 单个目录删不掉不该中断整轮
                logger.warning(f"===> failed to prune {p}: {e}")
                continue
            # 合集没了，它的 feed 也失去了意义（里面的 episode 全部 404）。
            # new 合集共用 rss/new.xml，不能按 sid 删。
            if kind in ("season", "series"):
                feed = _feed_path(output_root, kind, p.name)
                try:
                    if feed.exists():
                        feed.unlink()
                        logger.info(f"===> pruned stale feed {feed}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"===> failed to prune feed {feed}: {e}")
        deleted[kind] = names
    return deleted


def prune(
    config_path: Path | str,
    output_root: str | Path = "output",
    backend: str | None = None,
) -> PruneResult:
    config = load_active_config(config_path)
    sids_by_kind = _sids(config)
    root = resolve_root(config, output_root, backend)
    deleted = _do_prune(root, sids_by_kind)
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
    add_backend_argument(parser)
    args = parser.parse_args(argv)

    try:
        config = load_active_config(args.config)
    except ConfigError as e:
        print(f"error: {e}")
        return 1

    sids_by_kind = _sids(config)
    output_root = resolve_root(config, args.output_root, args.backend)

    if args.dry_run:
        for kind in _KINDS:
            names, _ = _scan(output_root, kind, sids_by_kind[kind])
            print(_format_dry_run(kind, names))
            if kind in ("season", "series"):
                feeds = [
                    str(_feed_path(output_root, kind, sid))
                    for sid in names
                    if _feed_path(output_root, kind, sid).exists()
                ]
                if feeds:
                    print(f"would delete ({kind} feeds): {sorted(feeds)}")
                orphans = _scan_orphan_feeds(output_root, kind, sids_by_kind[kind])
                if orphans:
                    print(
                        f"would delete (orphan {kind} feeds): "
                        f"{sorted(str(f) for f in orphans)}"
                    )
        return 0

    deleted = _do_prune(output_root, sids_by_kind)
    orphans = _prune_orphan_feeds(output_root, sids_by_kind)
    for kind in _KINDS:
        print(f"deleted ({kind}): {deleted[kind]}")
    for kind in ("season", "series"):
        if orphans[kind]:
            print(f"deleted (orphan {kind} feeds): {sorted(orphans[kind])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
