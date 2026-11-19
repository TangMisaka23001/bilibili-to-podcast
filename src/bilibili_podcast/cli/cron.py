"""b2p-cron: 24h scheduler for the pipeline.

远端（对象存储）后端下整条流水线都写同一个桶根，所以每个阶段必须拿到
同一个 backend：这里把后端解析出来，再以 ``--backend`` 参数透传给各阶段，
避免各阶段各自按配置/env 解析出不同结果。
"""
from __future__ import annotations

import argparse
import time

import schedule

from bilibili_podcast.config_loader import (
    BACKEND_CHOICES,
    add_backend_argument,
    load_yaml_config,
)
from bilibili_podcast.logger import get_logger
from bilibili_podcast.store import resolve_backend

logger = get_logger()


def _run_pipeline(backend: str | None = None, config_path: str = "config.yaml") -> None:
    """跑一轮流水线。

    ``backend`` 为 None 时各阶段沿用配置/env 的默认后端；显式给出时
    （例如 cron 自己的 ``--backend s3``）透传给每个阶段，保证一轮之内
    所有阶段写同一个地方。``config_path`` 同样透传，避免 cron 用 A 配置
    而某个阶段读了 B 配置。
    """
    from bilibili_podcast.cli.fetch import main as fetch_main
    from bilibili_podcast.cli.gen_index import main as gen_index_main
    from bilibili_podcast.cli.prune import main as prune_main
    from bilibili_podcast.cli.rss_cmd import main as rss_main
    from bilibili_podcast.cli.summarize import main as summarize_main
    from bilibili_podcast.cli.sync import main as sync_main

    # 各阶段的 --config 默认值是 "config.yaml"，这里显式传下去保持一致。
    extra = ["--config", config_path]
    if backend:
        extra += ["--backend", backend]

    prune_main(extra)
    fetch_main(extra)
    summarize_main(extra)
    rss_main(extra)
    # index.html must exist before sync mirrors output/ so it lands in the bucket
    gen_index_main(extra)
    sync_main(extra)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    add_backend_argument(parser)
    args = parser.parse_args(argv)

    config = load_yaml_config(args.config)
    backend = resolve_backend(config, args.backend)
    # 后端别名（object / fs 等）不在 CLI 的 choices 里，此时不强制透传，
    # 让各阶段自己按配置解析，避免 argparse 拒绝取值。
    stage_backend = backend if backend in BACKEND_CHOICES else None
    if backend != "local":
        logger.info(f"Pipeline backend: {backend}")

    schedule.every(24).hours.do(_run_pipeline, stage_backend, args.config)
    logger.info("Scheduled pipeline every 24 hours.")
    _run_pipeline(stage_backend, args.config)
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
