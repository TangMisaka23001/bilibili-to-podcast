"""Tests for bilibili_podcast.cli.gen_index."""
from __future__ import annotations

from bilibili_podcast.cli.gen_index import _proxy_cover


def test_proxy_cover_strips_https_and_adds_proxy():
    assert _proxy_cover("https://i0.hdslb.com/bfs/cover/abc.jpg") == \
        "https://images.weserv.nl/?url=i0.hdslb.com/bfs/cover/abc.jpg"


def test_proxy_cover_strips_http():
    assert _proxy_cover("http://example.com/x.jpg") == \
        "https://images.weserv.nl/?url=example.com/x.jpg"


def test_proxy_cover_empty_returns_empty():
    assert _proxy_cover("") == ""
