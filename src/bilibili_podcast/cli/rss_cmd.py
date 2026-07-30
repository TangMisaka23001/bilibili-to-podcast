"""b2p-rss: generate RSS XML feeds from local output/."""
from __future__ import annotations

import argparse

from bilibili_podcast.config_loader import load_active_config
from bilibili_podcast.rss import generate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate RSS XML feeds")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    parser.add_argument(
        "--new-top-n",
        type=int,
        default=5,
        help="Number of newest videos per new-array sid to include in rss/new.xml",
    )
    args = parser.parse_args(argv)

    config = load_active_config(args.config)
    generate(config, output_root=args.output_root, top_n=args.new_top_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
