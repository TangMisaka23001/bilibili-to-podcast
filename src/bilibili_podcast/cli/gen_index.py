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
  :root { --bg: #f2f2f7; --card-bg: #fff; --text: #1d1d1f; --sub: #86868b; --accent: #007aff; --btn-bg: #f2f2f7; --btn-hover: #e5e5ea; --tag-bg: #e8f0fe; --tag-text: #1a73e8; }
  @media (prefers-color-scheme: dark) { :root { --bg: #000; --card-bg: #1c1c1e; --text: #f5f5f7; --sub: #98989d; --accent: #0a84ff; --btn-bg: #2c2c2e; --btn-hover: #3a3a3c; --tag-bg: #1a3a5c; --tag-text: #7ab8ff; } }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .container { max-width: 960px; margin: 0 auto; padding: 48px 20px 80px; }
  .page-header { text-align: center; margin-bottom: 40px; }
  .page-header h1 { font-size: 2em; font-weight: 700; letter-spacing: -0.5px; }
  .page-header p { color: var(--sub); margin-top: 6px; font-size: 0.95em; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 20px; }
  .card { background: var(--card-bg); border-radius: 20px; overflow: hidden; display: flex; flex-direction: column; transition: transform 0.2s, box-shadow 0.2s; }
  .card:hover { transform: translateY(-4px); box-shadow: 0 12px 32px rgba(0,0,0,0.12); }
  .card-cover { aspect-ratio: 1; width: 100%; background: linear-gradient(135deg, #e0e0e0, #ccc); display: flex; align-items: center; justify-content: center; overflow: hidden; cursor: pointer; position: relative; }
  .card-cover img { width: 100%; height: 100%; object-fit: cover; }
  .card-cover .placeholder { width: 52px; height: 52px; opacity: 0.3; }
  .card-body { padding: 14px 16px 12px; display: flex; flex-direction: column; min-width: 0; flex: 1; }
  .card-body h2 { font-size: 0.95em; font-weight: 600; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 4px; }
  .card-body .author { font-size: 0.82em; color: var(--sub); margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card-body .bottom { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
  .card-body .tag { font-size: 0.72em; background: var(--tag-bg); color: var(--tag-text); padding: 2px 8px; border-radius: 8px; font-weight: 500; }
  .card-body .tag.season { background: #e8f0fe; color: #1a73e8; }
  .card-body .tag.series { background: #fce8e6; color: #ea4335; }
  @media (prefers-color-scheme: dark) { .card-body .tag.season { background: #1a3a5c; color: #7ab8ff; } .card-body .tag.series { background: #5c1a1a; color: #ff7a7a; } }
  .copy-btn { background: var(--btn-bg); border: none; color: var(--accent); font-size: 0.78em; font-weight: 500; padding: 6px 14px; border-radius: 20px; cursor: pointer; transition: background 0.15s; white-space: nowrap; }
  .copy-btn:hover { background: var(--btn-hover); }
  .copy-btn.copied { color: #34c759; }
  .toast { position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%); background: var(--text); color: var(--bg); padding: 10px 24px; border-radius: 20px; font-size: 0.88em; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  @media (max-width: 520px) { .cards { grid-template-columns: repeat(2, 1fr); gap: 12px; } .card-body { padding: 10px 12px; } }
\


_JS = r"""\
function copyRSS(url, btn) {
  navigator.clipboard.writeText(url).then(() => {
    btn.textContent = '\u2714 已复制';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = '复制链接'; btn.classList.remove('copied'); }, 2000);
  });
}\
"""


def _html(entry_list: list[_Entry]) -> str:
    cards = "\n".join(
        f"""<div class="card">
<div class="card-cover" onclick="window.open('{e.link}')">{'<img src="' + e.cover + '" alt="" loading="lazy">' if e.cover else '<svg class="placeholder" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15v-6l5 3-5 3z"/></svg>'}</div>
<div class="card-body">
<h2>{e.title}</h2>
<div class="author">{e.author}</div>
<div class="bottom">
<span class="tag {'season' if e.kind == 'season' else 'series'}">{'合集' if e.kind == 'season' else '系列'}</span>
<button class="copy-btn" onclick="copyRSS('{e.link}',this)">复制链接</button>
</div>
</div>
</div>"""
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
<script>
{_JS}
</script>
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
