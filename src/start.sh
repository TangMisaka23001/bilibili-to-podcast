#!/bin/bash
cd "$(dirname "$0")"  # ensure cwd = src/ for legacy script imports
PYTHONPATH=.. python -m src.tools.prune_output && \
  python bilibili_season.py && \
  python bilibili_series.py && \
  python upload_r2.py && \
  python bilibili_rss.py && \
  python upload_r2.py
