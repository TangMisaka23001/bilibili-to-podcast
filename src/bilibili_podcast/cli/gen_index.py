"""b2p-gen-index: generate docs/index.html from config sources."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from bilibili_api.channel_series import ChannelSeries, ChannelSeriesType

from bilibili_podcast.config_loader import load_active_config
from bilibili_podcast.logger import get_logger

logger = get_logger()

_RSS_BEAUTY = "https://rss.beauty/rss?url="


@dataclass(frozen=True)
class _Entry:
    kind: str
    sid: str
    title: str
    author: str
    cover: str
    link: str


def _fetch_meta(sid: str, uid: str, kind: str) -> dict:
    stype = ChannelSeriesType.SEASON if kind == "season" else ChannelSeriesType.SERIES
    cs = ChannelSeries(id_=sid, uid=uid, type_=stype)
    return asyncio.run(cs.get_meta())


def _build_entry(kind: str, sid: str, uid: str, prefix: str) -> _Entry:
    meta = _fetch_meta(sid, uid, kind)
    if kind == "season":
        title = meta.get("title") or f"合集 {sid}"
        author = meta.get("upper", {}).get("name") or f"UP {uid}"
        cover = meta.get("cover", "")
    else:
        title = meta.get("name") or f"系列 {sid}"
        author = meta.get("upper", {}).get("name", "") or meta.get("name", "") or f"UP {uid}"
        cover = meta.get("cover", "")
    link = f"https://www.bilibili.com/video/av{sid}" if not uid else ""
    return _Entry(
        kind=kind,
        sid=sid,
        title=title,
        author=author,
        cover=cover,
        link=f"{_RSS_BEAUTY}{prefix}/rss/{kind}/{sid}.xml",
    )


_CSS = """\
  :root { --bg: #f5f5f7; --card-bg: #fff; --text: #1d1d1f; --sub: #86868b; --accent: #0071e3; --tag-bg: #e8f0fe; --tag-text: #1a73e8; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1c1c1e; --card-bg: #2c2c2e; --text: #f5f5f7; --sub: #98989d; --accent: #4da6ff; --tag-bg: #1a3a5c; --tag-text: #7ab8ff; } }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 720px; margin: 0 auto; padding: 40px 20px 60px; }
  .page-header { text-align: center; margin-bottom: 36px; }
  .page-header h1 { font-size: 2em; font-weight: 700; letter-spacing: -0.5px; }
  .page-header p { color: var(--sub); margin-top: 6px; font-size: 0.95em; }
  .cards { display: grid; gap: 16px; }
  .card { background: var(--card-bg); border-radius: 16px; overflow: hidden; display: flex; transition: transform 0.15s, box-shadow 0.15s; text-decoration: none; color: inherit; }
  .card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.10); }
  .card-cover { width: 120px; min-height: 120px; flex-shrink: 0; background: #e0e0e0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .card-cover img { width: 100%; height: 100%; object-fit: cover; }
  .card-cover .placeholder { width: 40px; height: 40px; opacity: 0.25; }
  .card-body { padding: 16px 20px; display: flex; flex-direction: column; justify-content: center; min-width: 0; flex: 1; }
  .card-body h2 { font-size: 1.1em; font-weight: 600; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 6px; }
  .card-body .author { font-size: 0.88em; color: var(--sub); margin-bottom: 8px; }
  .card-body .tag { display: inline-block; font-size: 0.75em; background: var(--tag-bg); color: var(--tag-text); padding: 2px 10px; border-radius: 10px; font-weight: 500; align-self: flex-start; }
  @media (max-width: 500px) {
    .card-cover { width: 90px; min-height: 90px; }
    .card-body { padding: 12px 14px; }
    .card-body h2 { font-size: 1em; }
  }\
"""


def _html(entry_list: list[_Entry]) -> str:
    cards = "\n".join(
        f"""<a class="card" href="{e.link}">
<div class="card-cover">{'<img src="' + e.cover + '" alt="" loading="lazy">' if e.cover else '<svg class="placeholder" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15v-6l5 3-5 3z"/></svg>'}</div>
<div class="card-body">
<h2>{e.title}</h2>
<div class="author">{e.author}</div>
<span class="tag">{'合集' if e.kind == 'season' else '系列'}</span>
</div>
</a>"""
        for e in entry_list
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>bilibili podcast</title>
<style>
{_CSS}
</style>
</head>
<body>
<div class="container">
<header class="page-header">
<h1>bilibili podcast</h1>
<p>B 站视频合集 · 播客订阅</p>
</header>
<div class="cards">
{cards}
</div>
</div>
</body>
</html>
"""


def generate(config_path: str, output: str) -> None:
    config = load_active_config(config_path)
    prefix = config["RSS_URL_PREFIX"].rstrip("/")

    entries: list[_Entry] = []
    for c in config["season"]:
        sid, uid = str(c["sid"]), str(c["uid"])
        entries.append(_build_entry("season", sid, uid, prefix))
    for c in config["series"]:
        sid, uid = str(c["sid"]), str(c["uid"])
        entries.append(_build_entry("series", sid, uid, prefix))

    Path(output).write_text(_html(entries), encoding="utf-8")
    logger.info(f"===> wrote {output} ({len(entries)} entries)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate docs/index.html from config")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="docs/index.html")
    args = parser.parse_args(argv)
    generate(args.config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
