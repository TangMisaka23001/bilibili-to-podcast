from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse, parse_qs


@dataclass(frozen=True)
class ChannelSource:
    url: str
    uid: str
    sid: str
    type: Literal["season", "series"]


_VALID_TYPES = ("season", "series")
_ALLOWED_HOSTS = ("space.bilibili.com", "www.bilibili.com", "bilibili.com")


def _require_int(value: str, field: str, url: str) -> None:
    if not value.isdigit():
        raise ValueError(f"{field} must be numeric in URL: {url}")


def _extract_type(query: str, url: str) -> str:
    type_values = parse_qs(query).get("type")
    if not type_values:
        raise ValueError(f"missing required ?type= in URL: {url}")
    type_ = type_values[0]
    if type_ not in _VALID_TYPES:
        raise ValueError(
            f"invalid type '{type_}' in URL (must be one of {_VALID_TYPES}): {url}"
        )
    return type_


def parse_url(url: str) -> ChannelSource:
    parsed = urlparse(url)

    if parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError(f"unsupported host '{parsed.hostname}' in URL: {url}")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 3 or parts[1] != "lists":
        raise ValueError(f"URL path must be '/{{uid}}/lists/{{sid}}': {url}")

    uid, sid = parts[0], parts[2]
    _require_int(uid, "uid", url)
    _require_int(sid, "sid", url)

    return ChannelSource(
        url=url,
        uid=uid,
        sid=sid,
        type=_extract_type(parsed.query, url),
    )


def parse_sources(urls: list[str]) -> list[ChannelSource]:
    return [parse_url(url) for url in urls]


def to_legacy_config(sources: list[ChannelSource]) -> dict:
    return {
        "season": [{"uid": s.uid, "sid": s.sid} for s in sources if s.type == "season"],
        "series": [{"uid": s.uid, "sid": s.sid} for s in sources if s.type == "series"],
    }
