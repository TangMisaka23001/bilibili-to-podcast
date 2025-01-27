import os
import json
from logger import logger
from string import Template
from xml_template import item_template, channel_template, feed_xml_template
from email.utils import formatdate
from config import (
    RSS_URL_PREFIX,
    AUDIO_FORMAT,
    load_config,
    bilibili_link_prefix,
    season_base_path,
    series_base_path,
    series_rss_path,
    season_rss_path,
)
from file import load_season_videos, load_season_video_meta

def get_channel_sid_list():
    return [str(channel["sid"]) for channel in load_config()["season"]]


def get_series_sid_list():
    return [str(series["sid"]) for series in load_config()["series"]]


def load_channel_meta(channel):
    file_path = season_base_path + str(channel) + "/meta.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_series_meta(series):
    file_path = series_base_path + str(series) + "/meta.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_channel_bilibili_link(uid, sid):
    return (f"https://space.bilibili.com/{str(uid)}/lists/{str(sid)}?type=season")


def get_series_bilibili_link(uid, sid):
    return (f"https://space.bilibili.com/{str(uid)}/lists/{str(sid)}?type=series")


def load_channel_videos(channel):
    file_path = season_base_path + str(channel) + "/videos.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_series_videos(series):
    file_path = series_base_path + str(series) + "/videos.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_channel_video_meta(channel, bv):
    path = season_base_path + str(channel) + "/" + bv + "/meta.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_series_video_meta(series, bv):
    path = series_base_path + str(series) + "/" + bv + "/meta.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def timestamp_to_date(timestamp):
    return formatdate(timestamp, localtime=False, usegmt=True)


def scan_channel_dir_to_generate_items_xml(channel):
    logger.info("===> start scan channel videos and generate item " + channel)
    items = []
    for video in load_season_videos(channel):
        bv = video["bvid"]
        #if os.path.isdir(season_base_path + str(channel) + "/" + bv):
        video_meta = load_season_video_meta(channel, bv)
        audio_path = f"{season_rss_path + str(channel)}/{bv}/{bv}.{AUDIO_FORMAT}"
        item = Template(item_template).substitute(
            {
                "title": video_meta["title"],
                "description": video_meta["desc"].replace("&", "&amp;"),
                "image": video_meta["pic"],
                "url": RSS_URL_PREFIX + audio_path,
                "duration": video_meta["duration"],
                # "length": os.path.getsize("../output/" + audio_path),
                "length": 0,
                "link": bilibili_link_prefix + bv,
                "date": timestamp_to_date(video_meta["pubdate"]),
            }
        )
        items.append(item)
    return items


def scan_series_dir_to_generate_items_xml(series):
    logger.info("===> start scan series videos and generate item " + series)
    items = []
    for video in load_series_videos(series):
        bv = video["bvid"]
        if os.path.isdir(series_base_path + str(series) + "/" + bv):
            video_meta = load_series_video_meta(series, bv)
            audio_path = f"{series_rss_path + str(series)}/{bv}/{bv}.{AUDIO_FORMAT}"
            item = Template(item_template).substitute(
                {
                    "title": video_meta["title"],
                    "description": video_meta["desc"].replace("&", "&amp;"),
                    "image": video_meta["pic"],
                    "url": RSS_URL_PREFIX + audio_path,
                    "duration": video_meta["duration"],
                    "length": os.path.getsize("../output/" + audio_path),
                    "link": bilibili_link_prefix + bv,
                    "date": timestamp_to_date(video_meta["pubdate"]),
                }
            )
            items.append(item)
    return items


def generate_channel_xml(channel):
    logger.info("===> start generate channel xml")
    channel_meta = load_channel_meta(channel)
    channel_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{channel}.xml",
            "author": channel_meta["upper"]["name"],
            "title": channel_meta["title"],
            "description": channel_meta["title"],
            "link": get_channel_bilibili_link(channel_meta["mid"], channel_meta["id"]),
            "category": "",
            "image": channel_meta["cover"],
            "items": "\n".join(scan_channel_dir_to_generate_items_xml(channel)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": channel_string})


def generate_series_xml(series):
    logger.info("===> start generate series xml")
    meta = load_series_meta(series)
    series_string = Template(channel_template).substitute(
        {
            "atom_link": f"{RSS_URL_PREFIX}rss/{series}.xml",
            "author": meta["name"],
            "title": meta["name"],
            "description": meta["description"],
            "link": get_series_bilibili_link(meta["mid"], meta["series_id"]),
            "category": "",
            "image": "",
            "items": "\n".join(scan_series_dir_to_generate_items_xml(series)),
        }
    )
    return Template(feed_xml_template).substitute({"channel": series_string})


def write_to_rss_xml(prefix, channel, text):
    base_path = "../output/rss/"
    path = base_path + str(prefix) + "/" + str(channel) + ".xml"
    if not os.path.exists(base_path + str(prefix)):
        os.makedirs(base_path + str(prefix))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# 根据已经下载的文件生成播客格式的xml
for channel in get_channel_sid_list():
    logger.info(
        "===> start generate rss xml file by channel info channel id: " + channel
    )
    write_to_rss_xml("season", channel, generate_channel_xml(channel))
    logger.info("===> generate rss xml file by channel info done. ")

# for series in get_series_sid_list():
#     logger.info(
#         "===> start generate rss xml file by channel info channel id: " + series
#     )
#     write_to_rss_xml("series", series, generate_series_xml(series))
#     logger.info("===> generate rss xml file by series info done. ")
