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

bilibili_link_prefix = "https://www.bilibili.com/video/"
series_base_path = "output/bilibili-series/"
season_base_path = "output/bilibili-season/"
series_rss_path = "bilibili-series/"
season_rss_path = "bilibili-season/"


def _load_global_config():
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    project_root = here.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from bilibili_podcast.config_loader import load_active_config

    global config, RSS_URL_PREFIX, ACCESS_KEY, SECRET_KEY, ENDPOINT_URL, BUCKET_NAME, PORT
    config_path = Path(os.environ.get("B2P_CONFIG_PATH") or here.parent.parent / "config.yaml")
    try:
        config = load_active_config(config_path)
        logger.info("===> load active config")
        logger.info(config)
    except Exception as e:
        logger.warning(f"===> config not loaded: {e}; using empty defaults")
        config = {}

    RSS_URL_PREFIX = os.environ.get("RSS_URL_PREFIX") or config.get("RSS_URL_PREFIX", "")
    ACCESS_KEY = os.environ.get("R2_ACCESS_KEY") or ""
    SECRET_KEY = os.environ.get("R2_SECRET_KEY") or ""
    ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL") or config.get("ENDPOINT_URL", "")
    BUCKET_NAME = os.environ.get("R2_BUCKET_NAME") or config.get("BUCKET_NAME", "")
    PORT = int(os.environ.get("PORT") or config.get("PORT") or 8000)


_load_global_config()
