#!/usr/bin/env python3
"""Run the pipeline once and report what happened.

Usage:
    python3 tools/weekly_run.py [--root PROJECT_ROOT] [--keep-snapshots]

The script is the mechanical part of the weekly cron job:

1. snapshot ``output/rss`` so we can tell what the feeds changed;
2. run ``start.sh`` and capture every log line;
3. work out which stage failed and why — an expired cookie or a network hiccup
   is expected and must not be "fixed", a traceback in our own code is a bug
   worth fixing;
4. snapshot ``output/rss`` again and diff the two.

It prints a machine readable summary (last lines are JSON) so the agent that
runs this can act on the result and then notify the user.

Attribution is per stage.  ``start.sh`` prints ``===> stage: <name>`` before
each stage and a ``===> stage summary: name=code ...`` line at the end, so the
log can be split back into stages.  Only a *failing* stage is classified:
during a healthy run yt-dlp still logs ``HTTP Error 567`` warnings that the
playurl fallback then recovers from, and reporting those as "network problems"
every single week would be noise, not signal.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIFF_TOOL = HERE / "rss_diff.py"

#: Hard limit for one pipeline run. A full re-download of every episode can take
#: a long time, but not hours — a hung stage must not block the weekly report
#: forever, so the run is killed and reported as a timeout.
RUN_TIMEOUT_SEC = 4 * 3600

#: Patterns that mean "the outside world misbehaved" — never a code bug.
NETWORK_PATTERNS = [
    (r"HTTP Error 5\d\d", "服务端返回 5xx"),
    (r"HTTP Error 412", "请求被风控拦截 (412)"),
    (r"HTTP Error 567", "CDN 拒绝匿名请求 (567)"),
    (r"\b567 Server Error", "CDN 拒绝匿名请求 (567)"),
    (r"\b(?:412|429|503) Server Error", "CDN 返回错误状态"),
    (r"code\": ?-352", "B站风控拦截 (-352)"),
    (r"Connection (?:reset|aborted|refused|timed out)", "连接被中断"),
    (r"Read timed out|timed out|Timeout", "请求超时"),
    (r"Max retries exceeded|Temporary failure in name resolution", "网络不可达"),
    (r"Remote end closed connection", "远端断开连接"),
    (r"SSL|TLS", "TLS 握手失败"),
    (r"curl:\s*\(\d+\)", "curl 传输错误"),
    (r"Name or service not known|Temporary failure", "域名解析失败"),
    (r"download interrupted at \d+ bytes", "下载被中断"),
]

#: Patterns that mean "our cookie is no longer usable" — the user has to renew
#: it, we cannot fix that from code.
COOKIE_PATTERNS = [
    (r"code\": ?-101", "账号未登录 (-101)"),
    (r"no SESSDATA|SESSDATA", "cookie 中缺少/失效 sessdata"),
    (r"账号.*(失效|过期|封禁)", "账号状态异常"),
    (r"credential", "凭据缺失"),
]

#: Patterns that mean "our own code is broken".
CODE_PATTERNS = [
    (r"Traceback \(most recent call last\)", "Python 抛出未捕获异常"),
    (r"^\s*File \"[^\"]+\", line \d+", "异常栈帧"),
    (r"\b(?:KeyError|TypeError|AttributeError|ValueError|IndexError|NameError|"
     r"FileNotFoundError|PermissionError|OSError|JSONDecodeError|AssertionError|"
     r"ModuleNotFoundError|ImportError)\b", "代码异常类型"),
    (r"===> stage \S+ failed with exit code", "阶段执行失败"),
]

STAGE_MARKER = re.compile(r"^===> stage: (\S+)\s*$")
STAGE_SUMMARY = re.compile(r"^===> stage summary: (.+)$")


def _classify(text: str) -> list[dict]:
    """Bucket log lines into cookie / network / code buckets.

    A line can belong to more than one bucket, but each bucket keeps its own
    short list so the agent can decide what to do with it.
    """
    buckets: dict[str, list[dict]] = {"cookie": [], "network": [], "code": []}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for name, patterns in (("cookie", COOKIE_PATTERNS),
                               ("network", NETWORK_PATTERNS),
                               ("code", CODE_PATTERNS)):
            for pattern, label in patterns:
                if re.search(pattern, stripped):
                    buckets[name].append({"label": label, "line": stripped[:400]})
                    break
    return buckets


def _split_stages(log_text: str) -> dict[str, str]:
    """Split the log into per-stage chunks using start.sh's stage markers."""
    stages: dict[str, list[str]] = {}
    current = "startup"
    for line in log_text.splitlines():
        marker = STAGE_MARKER.match(line.strip())
        if marker:
            current = marker.group(1)
            stages.setdefault(current, [])
            continue
        stages.setdefault(current, []).append(line)
    return {name: "\n".join(lines) for name, lines in stages.items()}


def _stage_codes(log_text: str) -> dict[str, int]:
    """Read the exit code of every stage from start.sh's summary line."""
    codes: dict[str, int] = {}
    for line in log_text.splitlines():
        match = STAGE_SUMMARY.match(line.strip())
        if not match:
            continue
        for item in match.group(1).split():
            if "=" not in item:
                continue
            name, _, value = item.partition("=")
            try:
                codes[name] = int(value)
            except ValueError:
                continue
    return codes


def _snapshot(rss_dir: Path, target: Path) -> None:
    if target.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    if not rss_dir.is_dir():
        return
    for path in rss_dir.rglob("*.xml"):
        dest = target / path.relative_to(rss_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())


def _cleanup(directory: Path) -> None:
    if not directory.is_dir():
        return
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    directory.rmdir()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(HERE.parent))
    parser.add_argument("--keep-snapshots", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    reports = root / "reports"
    reports.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = reports / f"run-{stamp}.log"
    diff_path = reports / f"rss-diff-{stamp}.txt"
    before_dir = reports / f"rss-before-{stamp}"
    after_dir = reports / f"rss-after-{stamp}"

    rss_dir = root / "output" / "rss"
    _snapshot(rss_dir, before_dir)

    print(f"[weekly_run] running start.sh in {root} …", flush=True)
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            ["bash", "start.sh"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # one stream keeps the stage order intact
            text=True,
            timeout=RUN_TIMEOUT_SEC,
        )
        returncode = proc.returncode
        log_text = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        log_text = exc.stdout or ""
        if isinstance(log_text, bytes):
            log_text = log_text.decode("utf-8", "replace")
        log_text += f"\n[weekly_run] timeout after {RUN_TIMEOUT_SEC}s\n"
    elapsed = time.time() - started
    log_path.write_text(log_text, encoding="utf-8")

    _snapshot(rss_dir, after_dir)
    diff_proc = subprocess.run(
        [sys.executable, str(DIFF_TOOL), str(before_dir), str(after_dir)],
        capture_output=True,
        text=True,
    )
    diff_text = diff_proc.stdout or ""
    diff_path.write_text(diff_text, encoding="utf-8")

    codes = _stage_codes(log_text)
    failed_stages = {name: code for name, code in codes.items() if code != 0}
    stage_logs = _split_stages(log_text)

    # Only a failing stage is worth classifying. A healthy run still logs
    # recoverable yt-dlp warnings, and those are not "the pipeline failed".
    failed_logs = "\n".join(
        stage_logs.get(name, "") for name in failed_stages
    ) if failed_stages else ""
    buckets = _classify(failed_logs)

    # A traceback anywhere in a failing stage means our code is at fault, even
    # if the same stage also logged a network hiccup.
    code_signal = bool(buckets["code"])

    summary = {
        "ok": returncode == 0 and not failed_stages,
        "exit_code": returncode,
        "timed_out": timed_out,
        "elapsed_sec": round(elapsed, 1),
        "stage_codes": codes,
        "failed_stages": failed_stages,
        "log": str(log_path),
        "diff": str(diff_path),
        "cookie_issues": buckets["cookie"],
        "network_issues": buckets["network"],
        "code_issues": buckets["code"],
        "verdict": (
            "code" if code_signal
            else "cookie" if buckets["cookie"]
            else "network" if buckets["network"]
            else "unknown"
        ) if failed_stages else "ok",
        "diff_text": diff_text,
        "log_tail": log_text.splitlines()[-40:],
    }

    if not args.keep_snapshots:
        _cleanup(before_dir)
        _cleanup(after_dir)

    print(f"[weekly_run] exit={returncode} elapsed={elapsed:.0f}s")
    print(f"[weekly_run] stages: {codes}")
    print(f"[weekly_run] log: {log_path}")
    print(f"[weekly_run] diff: {diff_path}")
    print("--- SUMMARY_JSON ---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
