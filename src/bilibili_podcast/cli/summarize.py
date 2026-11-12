"""b2p-summarize: 抓 B站 AI 视频总结（只处理 bilibili-new/ 里的视频）。

产物：每个视频目录的 ``summary.json`` + ``chapters.json``，供 b2p-rss 渲染
description 与 ``<podcast:chapters>``。B站未生成总结的视频会被标记并跳过，
间隔 ``--retry-days`` 天后再试。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from bilibili_api.exceptions import ResponseCodeException

from bilibili_podcast.bilibili.credential import load_credential
from bilibili_podcast.bilibili.summary import (
    RETRY_AFTER_DAYS,
    embed_chapters,
    summarize_video,
)
from bilibili_podcast.config import AUDIO_FORMAT
from bilibili_podcast.logger import get_logger

logger = get_logger()


def _video_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("*/*") if (p / "meta.json").is_file())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch B站 AI summaries for bilibili-new")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--force", action="store_true", help="忽略已有结果，全部重抓")
    parser.add_argument(
        "--retry-days",
        type=int,
        default=RETRY_AFTER_DAYS,
        help="没取到总结的视频隔多少天重试（默认 7）",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.6,
        help="每个视频之间的间隔秒数，用来避开风控",
    )
    args = parser.parse_args(argv)

    root = Path(args.output_root) / "bilibili-new"
    if not root.is_dir():
        logger.info(f"===> {root} not found, nothing to summarize")
        return 0

    credential = load_credential()
    if credential is None or not credential.sessdata:
        logger.error(
            "===> AI 总结接口需要登录 Cookie（匿名返回 -101）；"
            "请配置 B2P_COOKIE_CONTENT 或 ./cookie"
        )
        return 1

    video_dirs = _video_dirs(root)
    available = 0
    for index, video_dir in enumerate(video_dirs):
        try:
            summary = summarize_video(
                video_dir, force=args.force, retry_days=args.retry_days
            )
        except ResponseCodeException as e:
            # 登录态失效会一路 -101，继续跑只会刷屏
            logger.error(f"===> {video_dir.parent.name}/{video_dir.name}: {e}")
            return 1
        except Exception as e:  # noqa: BLE001 - 单视频失败不中断整轮
            logger.error(f"===> {video_dir.parent.name}/{video_dir.name}: {e}")
        else:
            if summary is not None and summary.available:
                available += 1
                # 章节同时写进音频文件本身：RSS 的 podcast:chapters 只有
                # iOS/iPadOS 26.2+ 才读，文件内嵌章节所有版本都认。
                embed_chapters(video_dir, video_dir.name, AUDIO_FORMAT)
        if index < len(video_dirs) - 1:
            time.sleep(args.sleep)

    logger.info(f"===> summarize done: {available}/{len(video_dirs)} 有 AI 总结")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
