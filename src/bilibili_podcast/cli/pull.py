"""b2p-pull: restore the local output/ tree from the R2 bucket.

This is the counterpart of ``b2p-sync``: instead of uploading the local tree
to the bucket, it downloads the bucket's objects into the local tree. It is
how a fresh machine (or a CI runner) picks up the already-published episodes
without re-downloading every audio file from Bilibili — the bucket already
holds the ``complete`` markers that tell the fetch pipeline to skip what it
has.
"""
from __future__ import annotations

import argparse

from bilibili_podcast.config_loader import ConfigError, load_active_config
from bilibili_podcast.storage import make_s3_client, pull


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore output/ from R2")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "Only pull keys starting with this prefix; repeatable. "
            "Defaults to the whole bucket."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel downloads",
    )
    args = parser.parse_args(argv)

    try:
        config = load_active_config(args.config)
    except ConfigError as e:
        print(f"error: {e}")
        return 1

    r2 = config.get("R2")
    if not r2:
        print(f"error: no R2 section in {args.config}")
        return 1

    client = make_s3_client(r2["ACCESS_KEY"], r2["SECRET_KEY"], r2["ENDPOINT_URL"])
    result = pull(
        args.output_root,
        r2["BUCKET_NAME"],
        client,
        prefixes=tuple(args.prefix),
        workers=args.workers,
    )
    print(
        f"downloaded={len(result.downloaded)} "
        f"skipped={len(result.skipped)} "
        f"failed={len(result.failed)} "
        f"bytes={result.bytes_downloaded}"
    )
    if result.failed:
        print("failed keys: " + ", ".join(result.failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
