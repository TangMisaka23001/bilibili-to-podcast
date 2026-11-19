"""Global config: load ../config.yaml once at module import.

`config.yaml` is optional — all settings can be provided via environment
variables (RSS_URL_PREFIX, PORT, R2_*). Read top-level scalars into
module-level variables. The active `season` / `series` collection lists are
resolved by bilibili_podcast.config_loader from `sources.json`.
"""
import os

from bilibili_podcast.logger import get_logger

logger = get_logger()


RSS_URL_PREFIX = ""
AUDIO_FORMAT = 'm4a'
ACCESS_KEY = ""
SECRET_KEY = ""
ENDPOINT_URL = ""
BUCKET_NAME = ""
PORT = 8000
config = {}

#: Normalize freshly downloaded audio to the podcast loudness reference.
#: Existing episodes keep the loudness they were published with, so this only
#: affects new downloads.
NORMALIZE_LOUDNESS = True

bilibili_link_prefix = "https://www.bilibili.com/video/"

COOKIE_ENV_VAR = "B2P_COOKIE_CONTENT"


def _as_bool(value: object, default: bool = True) -> bool:
    """Parse a YAML/env flag that may be a bool, a number, or a string."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _load_global_config():
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    project_root = here.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from bilibili_podcast.config_loader import load_active_config

    global config, RSS_URL_PREFIX, ACCESS_KEY, SECRET_KEY, ENDPOINT_URL, BUCKET_NAME, PORT, NORMALIZE_LOUDNESS
    config_path = Path(os.environ.get("B2P_CONFIG_PATH") or here.parent.parent / "config.yaml")
    try:
        config = load_active_config(config_path)
        logger.info("===> load active config")
        # Never log the whole config: it contains R2 credentials.
        logger.info(
            "===> season=%d series=%d new=%d",
            len(config.get("season") or []),
            len(config.get("series") or []),
            len(config.get("new") or []),
        )
    except Exception as e:
        logger.warning(f"===> config not loaded: {e}; using empty defaults")
        config = {}

    RSS_URL_PREFIX = os.environ.get("RSS_URL_PREFIX") or config.get("RSS_URL_PREFIX", "")
    ACCESS_KEY = os.environ.get("R2_ACCESS_KEY") or ""
    SECRET_KEY = os.environ.get("R2_SECRET_KEY") or ""
    ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL") or config.get("ENDPOINT_URL", "")
    BUCKET_NAME = os.environ.get("R2_BUCKET_NAME") or config.get("BUCKET_NAME", "")
    PORT = int(os.environ.get("PORT") or config.get("PORT") or 8000)
    # 响度归一化开关：环境变量优先，其次 config.yaml 的 audio.normalize_loudness。
    audio_cfg = config.get("audio") or {}
    env_flag = os.environ.get("B2P_NORMALIZE_LOUDNESS")
    if env_flag is not None:
        NORMALIZE_LOUDNESS = _as_bool(env_flag, True)
    else:
        NORMALIZE_LOUDNESS = _as_bool(audio_cfg.get("normalize_loudness"), True)


_load_global_config()
