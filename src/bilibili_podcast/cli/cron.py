"""b2p-cron: 24h scheduler for the pipeline."""
from __future__ import annotations

import time

import schedule

from bilibili_podcast.logger import get_logger

logger = get_logger()


def _run_pipeline() -> None:
    from bilibili_podcast.cli.fetch import main as fetch_main
    from bilibili_podcast.cli.prune import main as prune_main
    from bilibili_podcast.cli.sync import main as sync_main

    prune_main([])
    fetch_main([])
    sync_main([])


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    schedule.every(24).hours.do(_run_pipeline)
    logger.info("Scheduled pipeline every 24 hours.")
    _run_pipeline()
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
