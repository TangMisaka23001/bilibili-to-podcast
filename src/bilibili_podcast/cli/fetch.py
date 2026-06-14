"""b2p-fetch: pull Bilibili collection audio + meta into local output."""
from __future__ import annotations

import argparse
from pathlib import Path

from bilibili_podcast.bilibili.channel import ChannelRef, ChannelType, fetch_all
from bilibili_podcast.config_loader import load_active_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch B站 collections")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    args = parser.parse_args(argv)

    config = load_active_config(args.config)
    refs = [
        ChannelRef(type=ChannelType.SEASON, uid=c["uid"], sid=c["sid"])
        for c in config["season"]
    ] + [
        ChannelRef(type=ChannelType.SERIES, uid=c["uid"], sid=c["sid"])
        for c in config["series"]
    ]
    fetch_all(refs, output_root=Path(args.output_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
