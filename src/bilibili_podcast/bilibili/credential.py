"""Bilibili credential loading — single source of truth for cookie handling.

Two consumers need cookies:

* ``bilibili_api`` (collection meta/list + video detail) -> :func:`load_credential`
* ``yt-dlp`` (audio download) -> :func:`materialize_cookie_file`

Both read from ``B2P_COOKIE_CONTENT`` (CI) or the conventional ``./cookie``
file (local), so only this module knows where cookies live.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from bilibili_api.utils.network import Credential

from bilibili_podcast.config import COOKIE_ENV_VAR

COOKIE_FILE_NAME = "cookie"

#: Netscape cookie names mapped onto ``Credential`` keyword arguments.
_CREDENTIAL_FIELDS = {
    "SESSDATA": "sessdata",
    "bili_jct": "bili_jct",
    "buvid3": "buvid3",
    "buvid4": "buvid4",
    "DedeUserID": "dedeuserid",
    "ac_time_value": "ac_time_value",
}


def cookie_text() -> str | None:
    """Return raw cookie content: ``B2P_COOKIE_CONTENT`` first, then ``./cookie``."""
    content = os.environ.get(COOKIE_ENV_VAR)
    if content:
        return content
    path = Path(COOKIE_FILE_NAME)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def parse_cookie_text(text: str) -> dict[str, str]:
    """Parse Netscape cookie content into a ``name -> value`` mapping.

    Handles the ``#HttpOnly_`` domain prefix that yt-dlp emits for HttpOnly
    cookies (SESSDATA among them) and ignores plain comment lines.
    """
    cookies: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        name, value = parts[5], parts[6]
        if name:
            cookies[name] = value
    return cookies


def load_credential() -> Credential | None:
    """Build a ``Credential`` from the configured cookie, or ``None`` when the
    cookie carries none of the fields bilibili_api recognises.

    ``None`` lets ``bilibili_api`` fall back to its anonymous default instead of
    sending an empty credential.
    """
    text = cookie_text()
    if not text:
        return None
    cookies = parse_cookie_text(text)
    values = {
        arg: cookies[name]
        for name, arg in _CREDENTIAL_FIELDS.items()
        if cookies.get(name)
    }
    if not values:
        return None
    return Credential(**values)


def materialize_cookie_file() -> tuple[str | None, bool]:
    """Return ``(path, is_temporary)`` for yt-dlp's ``cookiefile`` option.

    yt-dlp treats ``cookiefile`` as read/write and **dumps the cookie jar back
    to it on exit**, which silently destroys the source cookie (HttpOnly flags,
    extra cookies). To keep the configured cookie intact we always hand yt-dlp a
    throwaway copy and never the original path.

    Returns ``(None, False)`` when no cookie is configured, so yt-dlp runs
    anonymously instead of failing on a missing file.
    """
    text = cookie_text()
    if not text:
        return None, False
    fd, path = tempfile.mkstemp(prefix="b2p-cookie-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        os.unlink(path)
        raise
    return path, True
