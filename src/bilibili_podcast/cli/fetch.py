"""b2p-fetch: pull Bilibili collection audio + meta into local output."""
from __future__ import annotations

import argparse
from pathlib import Path

from bilibili_podcast.bilibili.channel import (
    ChannelRef,
    ChannelType,
    fetch_all,
    fetch_all_new,
)
from bilibili_podcast.config_loader import load_active_config


def _refs(config: dict, key: str) -> list[ChannelRef]:
    type_for_kind = {
        "season": ChannelType.SEASON,
        "series": ChannelType.SERIES,
    }
    return [
        ChannelRef(type=type_for_kind[c["type"]], uid=c["uid"], sid=c["sid"])
        for c in config.get(key, [])
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch B站 collections")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    parser.add_argument(
        "--new-top-n",
        type=int,
        default=5,
        help="Number of newest videos per new-array sid to fetch",
    )
    args = parser.parse_args(argv)

    config = load_active_config(args.config)
    output_root = Path(args.output_root)

    # `all` — fetch every video, full pipeline (season + series).
    fetch_all(_refs(config, "season") + _refs(config, "series"), output_root=output_root)

    # `new` — fetch only the newest N per sid into bilibili-new/.
    fetch_all_new(_refs(config, "new"), output_root=output_root, top_n=args.new_top_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())