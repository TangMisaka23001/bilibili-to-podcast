"""Global config: load ../config.yaml once at module import.

Read top-level scalars (`RSS_URL_PREFIX`, `PORT`, `R2.*`) into module-level
variables. The active `season` / `series` collection lists are resolved by
bilibili_podcast.config_loader so both legacy and `sources:` configs work.

Importing this module triggers a one-time yaml load. Lazy-imports the loader
so the file works whether the caller runs from inside src/ (legacy scripts)
or from the project root.
"""
from logger import logger


RSS_URL_PREFIX = ""
AUDIO_FORMAT = 'm4a'
ACCESS_KEY = ""
SECRET_KEY = ""
ENDPOINT_URL = ""
BUCKET_NAME = ""
PORT = 8000
config = {}

bilibili_link_prefix = "https://www.bilibili.com/video/"
series_base_path = "../output/bilibili-series/"
season_base_path = "../output/bilibili-season/"
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
    config = load_active_config("../config.yaml")
    logger.info("===> load config.yaml")
    logger.info(config)
    RSS_URL_PREFIX = config.get("RSS_URL_PREFIX", "")
    r2 = config.get("R2", {}) or {}
    ACCESS_KEY = r2.get("ACCESS_KEY", "")
    SECRET_KEY = r2.get("SECRET_KEY", "")
    ENDPOINT_URL = r2.get("ENDPOINT_URL", "")
    BUCKET_NAME = r2.get("BUCKET_NAME", "")
    PORT = config.get("PORT", 8000)


_load_global_config()
