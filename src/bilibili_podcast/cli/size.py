"""b2p-size: print the size of the output/ directory.

Useful as the last step of `start.sh` to see how much disk the pipeline
consumed on this run. Sub-directories (`bilibili-season/`, `bilibili-series/`,
`bilibili-new/`, `rss/`) are reported on separate lines so the user can
spot which kind dominates.

远端（对象存储）模式下量的是桶里的对象体积：走 `store.list_objects()` 累加
对象大小，而不是落本地再统计——远端直写模式下本地根本没有 output/ 目录。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from bilibili_podcast.config_loader import add_backend_argument, load_yaml_config
from bilibili_podcast.store import is_remote, resolve_root

_SUBDIRS = ("bilibili-season", "bilibili-series", "bilibili-new", "rss")


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += Path(root, f).stat().st_size
            except OSError:
                pass
    return total


def _humanize(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            if unit == "B":
                return f"{int(size)}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{int(size)}T"


def _remote_label(root: Any) -> str:
    """远端根目录的可读标识，例如 ``s3://my-bucket/podcast``。"""
    store = getattr(root, "store", None)
    bucket = str(getattr(store, "bucket", "") or "")
    prefix = str(getattr(store, "prefix", "") or "").strip("/")
    return f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"


def _remote_size(store: Any, prefix: str) -> int:
    """远端某个前缀下的对象总字节数。

    ``list_objects`` 返回的 key 是相对 store prefix 的（含查询前缀那一段），
    但 size 是对象真实大小，直接累加即可。
    """
    return sum(store.list_objects(prefix).values())


def report(output_root: str | Path = "output") -> None:
    """打印根目录与各子目录的体积。

    本地模式的输出格式保持与改造前逐字节一致；远端模式量的是桶里的对象
    体积，标识用 ``s3://bucket/prefix``。

    本地传本地路径，远端传 ``resolve_root()`` 给出的 ``S3Path``——由根的
    类型决定走哪条分支，所以这里不另设 backend 参数（免得两者打架）。
    """
    if is_remote(output_root):
        root = output_root
        label = _remote_label(root)
        if not root.exists():
            print(f"{label}/ not found")
            return
        store = root.store
        print(f"{label}/  total: {_humanize(_remote_size(store, ''))}")
        for sub in _SUBDIRS:
            size = _remote_size(store, sub)
            if size or (root / sub).is_dir():
                print(f"  {sub}/: {_humanize(size)}")
        return

    root = Path(output_root)
    if not root.is_dir():
        print(f"{output_root}/ not found")
        return

    total = _dir_size(root)
    print(f"{output_root}/  total: {_humanize(total)}")

    for sub in _SUBDIRS:
        p = root / sub
        if p.is_dir():
            print(f"  {sub}/: {_humanize(_dir_size(p))}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-root", default="output")
    add_backend_argument(parser)
    args = parser.parse_args(argv)

    config = load_yaml_config(args.config)
    root = resolve_root(config, args.output_root, args.backend)
    if is_remote(root):
        report(root)
    else:
        # 本地模式仍传原始字符串：``output_root`` 原样打印才能保证输出与
        # 改造前逐字节一致（例如 "./output/" 不会被 Path 规范化掉）。
        report(args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())