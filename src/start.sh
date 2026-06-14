#!/bin/bash
python -m src.tools.prune_output && python bilibili_season.py && python bilibili_series.py && python upload_r2.py && python bilibili_rss.py && python upload_r2.py
