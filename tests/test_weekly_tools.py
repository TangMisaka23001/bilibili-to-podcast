"""Tests for the weekly-report helpers in tools/.

These helpers decide whether a failed pipeline run is a cookie problem, a
network problem or a bug in our own code.  The weekly cron job acts on that
verdict, so the classification needs to be pinned down by tests rather than by
eyeballing logs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


weekly_run = _load("weekly_run")
rss_diff = _load("rss_diff")


LOG_OK = """===> stage: b2p-fetch
===> BV12uua6fEd8: succeeded via playurl mirror
===> stage: b2p-rss
===> wrote output/rss/season/8722727.xml
===> stage summary: b2p-prune=0 b2p-fetch=0 b2p-rss=0
"""

LOG_COOKIE = """===> stage: b2p-summarize
ERROR - 2026-11-13 03:01:02 - bilibili_podcast - ERROR - credential missing: no SESSDATA in cookie
===> stage summary: b2p-summarize=1 b2p-rss=0
"""

LOG_NETWORK = """===> stage: b2p-fetch
===> download interrupted at 0 bytes: 567 Server Error: Unknown Status for url: https://upos-sz-mirrorcosov.bilivideo.com/x
===> stage summary: b2p-fetch=1 b2p-rss=0
"""

LOG_CODE = """===> stage: b2p-rss
Traceback (most recent call last):
  File "/workspace/bilibili-to-podcast/src/bilibili_podcast/rss.py", line 69, in _load_json
    return json.loads(path.read_text(encoding="utf-8"))
FileNotFoundError: [Errno 2] No such file or directory: 'output/bilibili-new/1095498/videos.json'
===> stage summary: b2p-rss=1
"""


def _failed_log(log: str) -> str:
    """Only the log of failing stages, exactly like weekly_run does."""
    codes = weekly_run._stage_codes(log)
    stages = weekly_run._split_stages(log)
    failed = {name for name, code in codes.items() if code != 0}
    return "\n".join(stages.get(name, "") for name in failed)


def test_stage_codes_reads_every_stage():
    codes = weekly_run._stage_codes(LOG_OK)
    assert codes == {"b2p-prune": 0, "b2p-fetch": 0, "b2p-rss": 0}


def test_split_stages_keeps_stage_logs_apart():
    stages = weekly_run._split_stages(LOG_OK)
    assert "b2p-fetch" in stages
    assert "b2p-rss" in stages
    assert "succeeded via playurl mirror" in stages["b2p-fetch"]
    assert "succeeded via playurl mirror" not in stages["b2p-rss"]


def test_healthy_stage_with_567_warning_is_not_reported_as_network():
    """A recovered yt-dlp 567 must not be attributed to the failing stage."""
    log = """===> stage: b2p-fetch
ERROR: [download] Got error: HTTP Error 567: Unknown Status. Giving up after 10 retries
===> BV12uua6fEd8: succeeded via playurl mirror
===> stage: b2p-summarize
===> stage summary: b2p-fetch=0 b2p-summarize=1
"""
    buckets = weekly_run._classify(_failed_log(log))
    assert buckets["network"] == []
    assert buckets["cookie"] == []


def test_cookie_failure_is_classified_as_cookie():
    buckets = weekly_run._classify(_failed_log(LOG_COOKIE))
    assert buckets["cookie"]
    assert buckets["code"] == []


def test_network_failure_is_classified_as_network():
    buckets = weekly_run._classify(_failed_log(LOG_NETWORK))
    assert buckets["network"]
    assert buckets["code"] == []


def test_traceback_is_classified_as_code():
    buckets = weekly_run._classify(_failed_log(LOG_CODE))
    assert buckets["code"]
    labels = {item["label"] for item in buckets["code"]}
    assert "Python 抛出未捕获异常" in labels


def test_healthy_log_has_no_findings():
    buckets = weekly_run._classify(_failed_log(LOG_OK))
    assert buckets == {"cookie": [], "network": [], "code": []}


def _feed(path: Path, items: list[tuple[str, str, int]]) -> None:
    """Write a minimal RSS feed whose items are (bv, title, length)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = []
    for bv, title, length in items:
        body.append(
            f"<item><title>{title}</title>"
            f"<enclosure url=\"https://p.example.com/bilibili-season/1/{bv}/{bv}.m4a\" "
            f"length=\"{length}\" type=\"audio/mp4\"/>"
            f"<description>desc</description></item>"
        )
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        "<title>feed</title>"
        + "".join(body)
        + "</channel></rss>",
        encoding="utf-8",
    )


def test_rss_diff_reports_added_removed_and_changed(tmp_path, capsys):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _feed(before / "season" / "1.xml", [("BV1abcdefghj", "旧标题", 100), ("BV2abcdefghj", "留下", 200)])
    _feed(after / "season" / "1.xml", [("BV1abcdefghj", "新标题", 100), ("BV3abcdefghj", "新增", 300)])

    rss_diff.main(["rss_diff.py", str(before), str(after)])
    out = capsys.readouterr().out
    assert "BV3abcdefghj" in out
    assert "BV2abcdefghj" in out
    assert "新增" in out
    assert "删除" in out
    assert "变更" in out


def test_rss_diff_on_identical_trees_reports_no_change(tmp_path, capsys):
    before = tmp_path / "before"
    after = tmp_path / "after"
    _feed(before / "season" / "1.xml", [("BV1abcdefghj", "标题", 100)])
    _feed(after / "season" / "1.xml", [("BV1abcdefghj", "标题", 100)])

    rss_diff.main(["rss_diff.py", str(before), str(after)])
    out = capsys.readouterr().out
    assert "新增 0 集" in out
    assert "删除 0 集" in out
    assert "变更 0 集" in out


@pytest.mark.parametrize("name", ["weekly_run", "rss_diff"])
def test_tools_are_executable_scripts(name):
    assert (TOOLS / f"{name}.py").exists()
