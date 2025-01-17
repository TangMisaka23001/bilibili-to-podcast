from logger import logger
import yaml

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

def load_config():
    with open("../config.yaml", "r") as f:
        return yaml.safe_load(f)


def load_global_config():
    global config
    config = load_config()
    logger.info("===> load config.yaml")
    logger.info(config)
    global RSS_URL_PREFIX
    global FETCH_RECENT_N_VIDEOS
    RSS_URL_PREFIX = config["RSS_URL_PREFIX"]
    
    global ACCESS_KEY
    global SECRET_KEY
    global ENDPOINT_URL
    global BUCKET_NAME
    ACCESS_KEY = config["R2"]["ACCESS_KEY"]
    SECRET_KEY = config["R2"]["SECRET_KEY"]
    ENDPOINT_URL = config["R2"]["ENDPOINT_URL"]
    BUCKET_NAME = config["R2"]["BUCKET_NAME"]

    global PORT
    PORT = config["PORT"]
    
load_global_config()