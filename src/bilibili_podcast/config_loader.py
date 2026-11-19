"""Read config.yaml (optional) and sources.json, resolve the active config.

`sources.json` (sibling of `config.yaml`) holds the list of B站 collection
URLs to track. Top-level structure is an object with two arrays:

    {
        "all": [ "<url>", ... ],   // long-tracked collections
        "new": [ "<url>", ... ]    // newcomer / pending review
    }

Both lists are concatenated in order (`all` first, then `new`) and the
combined URLs are parsed into uid/sid/type, then bucketed into the
`season` and `series` keys of the returned config dict. The yaml file is
optional: when missing, the result is populated from environment variables
and defaults only. All other top-level yaml fields are passed through unchanged.

Two sections matter for the storage backend and are passed through verbatim:

    storage:
      backend: s3        # local | s3（r2 是 s3 的别名）
      prefix: ""        # 桶内前缀，可空

    R2:
      ACCESS_KEY: ...
      SECRET_KEY: ...
      ENDPOINT_URL: ...
      BUCKET_NAME: ...
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Union

import yaml

from bilibili_podcast.extract_url import parse_sources, to_legacy_config


class ConfigError(Exception):
    pass


#: ``--backend`` 的可选值。local=本地目录，s3/r2=对象存储（S3 兼容协议）。
BACKEND_CHOICES = ("local", "s3", "r2")


def add_backend_argument(parser: argparse.ArgumentParser) -> None:
    """给 CLI 加上 ``--backend``（显式覆盖配置/env 里的存储后端）。

    八个入口共用同一份取值与默认值，放在一处免得新增后端时漏改。
    默认 None 表示「按 config.yaml 的 storage.backend / 环境变量
    B2P_STORAGE_BACKEND 决定」。
    """
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        type=str.lower,
        default=None,
        help=(
            "存储后端：local 写本地目录，s3/r2 直接写对象存储；"
            "默认按 config.yaml 的 storage.backend 或环境变量 B2P_STORAGE_BACKEND 决定"
        ),
    )


def _read_yaml(config_path: Path) -> dict:
    """读 config.yaml；文件不存在时返回空 dict。"""
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file must be a mapping: {config_path}")
    return data


def _check_storage(config: dict) -> dict:
    """校验 ``storage`` 段：必须是映射，否则后端解析会炸在一句无关的报错上。"""
    storage = config.get("storage")
    if storage is not None and not isinstance(storage, dict):
        raise ConfigError(
            "config 'storage' must be a mapping "
            "(e.g. 'storage: {backend: s3, prefix: \"\"}'), "
            f"got {type(storage).__name__}"
        )
    return config


def load_yaml_config(config_path: Union[str, Path]) -> dict:
    """只读 config.yaml，不要求 sources.json 存在。

    给只需要 storage / R2 段的入口用（如 b2p-size）：那些命令不关心合集
    列表，不该因为 sources.json 缺失就整个跑不起来。
    """
    config_path = Path(config_path)
    return _check_storage(_read_yaml(config_path))


def _load_sources(sources_path: Path) -> tuple[list[str], list[str]]:
    """Read sources.json. Returns (all_urls, new_urls)."""
    try:
        with sources_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"sources file not found: {sources_path}") from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"sources file must be an object with 'all' and 'new' arrays: {sources_path}"
        )
    if "all" not in data and "new" not in data:
        raise ConfigError(
            f"sources file must contain at least one of 'all' / 'new' keys: {sources_path}"
        )

    all_urls: list[str] = []
    new_urls: list[str] = []
    for key, target in (("all", all_urls), ("new", new_urls)):
        value = data.get(key, [])
        if not isinstance(value, list):
            raise ConfigError(
                f"sources file '{key}' must be an array: {sources_path}"
            )
        target.extend(value)

    return all_urls, new_urls


def load_active_config(config_path: Union[str, Path]) -> dict:
    config_path = Path(config_path)
    config = _check_storage(_read_yaml(config_path))

    sources_path = config_path.parent / "sources.json"
    all_urls, new_urls = _load_sources(sources_path)

    try:
        parsed_all = parse_sources(all_urls)
        parsed_new = parse_sources(new_urls)
    except ValueError as e:
        raise ConfigError(str(e)) from e
    config["season"] = to_legacy_config(parsed_all)["season"]
    config["series"] = to_legacy_config(parsed_all)["series"]
    config["new"] = [{"uid": s.uid, "sid": s.sid, "type": s.type} for s in parsed_new]

    return config
