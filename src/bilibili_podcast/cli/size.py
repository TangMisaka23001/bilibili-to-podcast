"""b2p-size: print the size of the local output/ directory.

Useful as the last step of `start.sh` to see how much disk the pipeline
consumed on this run. Sub-directories (`bilibili-season/`, `bilibili-series/`,
`bilibili-new/`, `rss/`) are reported on separate lines so the user can
spot which kind dominates.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

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


def report(output_root: str | Path = "output") -> None:
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
    parser.add_argument("--output-root", default="output")
    args = parser.parse_args(argv)
    report(args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())