"""b2p-sync: mirror local output to R2."""
from __future__ import annotations

import argparse

from bilibili_podcast.config_loader import load_active_config
from bilibili_podcast.storage import make_s3_client, sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mirror output/ to R2")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    args = parser.parse_args(argv)

    config = load_active_config(args.config)
    r2 = config["R2"]
    client = make_s3_client(r2["ACCESS_KEY"], r2["SECRET_KEY"], r2["ENDPOINT_URL"])
    result = sync(args.output_root, r2["BUCKET_NAME"], client, force_prefixes=("rss",))
    print(
        f"uploaded={len(result.uploaded)} "
        f"deleted={len(result.deleted)} "
        f"skipped={len(result.skipped)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
