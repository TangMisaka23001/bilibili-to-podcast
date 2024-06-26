import yaml
import os
import json
import asyncio
from logger import logger
from string import Template
import requests
from email.utils import formatdate
from bilibili_api import channel_series, video as video_api
import yt_dlp
from xml_template import item_template, channel_template, feed_xml_template

RSS_URL_PREFIX = ""
NEWEST_VIDEOS_FIRST = 0
FETCH_RECENT_N_VIDEOS = False
AUDIO_FORMAT = 'm4a'
base_path = "bilibili-channel/"
bilibili_link_prefix = "https://www.bilibili.com/video/"


def full_path(path):
    return base_path + str(path)


def channel_mkdir(channel):
    path = full_path(channel)
    if not os.path.exists(path):
        os.makedirs(path)


def load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def load_global_config():
    config = load_config()
    logger.info("===> load config.yaml")
    logger.info(config)
    global RSS_URL_PREFIX
    global FETCH_RECENT_N_VIDEOS
    global NEWEST_VIDEOS_FIRST
    RSS_URL_PREFIX = config["RSS_URL_PREFIX"]
    FETCH_RECENT_N_VIDEOS = config["FETCH_RECENT_N_VIDEOS"]
    NEWEST_VIDEOS_FIRST = config["NEWEST_VIDEOS_FIRST"]


def get_channel_list(config):
    return config["channel"]


def get_channel_sid_list(config):
    return [str(channel["sid"]) for channel in get_channel_list(config)]


def get_channel_series(id, uid):
    return channel_series.ChannelSeries(
        id_=id, uid=uid, type_=channel_series.ChannelSeriesType.SEASON
    )


def get_channel_meta(series):
    return series.get_meta()


def wirte_channel_meta(channel, text):
    path = full_path(channel) + "/meta.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(text, indent=2, ensure_ascii=False))


def load_channel_meta(channel):
    file_path = full_path(channel) + "/meta.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def aget_videos(pn):
    if NEWEST_VIDEOS_FIRST:
        return await series.get_videos(pn=pn, sort=channel_series.ChannelOrder.CHANGE)
    return await series.get_videos(pn=pn)


async def get_channel_videos(channel_meta):
    series = get_channel_series(id=channel_meta["id"], uid=channel_meta["mid"])
    # 合集有分页，这里需要解开分页返回全部视频信息
    pn = 1
    result = []
    while True:
        page_videos = await series.get_videos(pn=pn)
        result += page_videos["archives"]
        if FETCH_RECENT_N_VIDEOS > 0 and len(result) >= FETCH_RECENT_N_VIDEOS:
            return result[:FETCH_RECENT_N_VIDEOS]
        if len(result) >= channel_meta["media_count"]:
            break
        pn += 1
    return result


def wirte_channel_videos(channel, text):
    path = full_path(channel) + "/videos.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(text, indent=2, ensure_ascii=False))


def load_channel_videos(channel):
    file_path = full_path(channel) + "/videos.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def channel_videos_mkdir(channel, bv):
    path = full_path(channel) + "/" + str(bv)
    if not os.path.exists(path):
        os.mkdir(path)


async def get_video_info(bv):
    return await video_api.Video(bvid=bv).get_info()


def wirte_channel_video_meta(channel, bv, text):
    path = full_path(channel) + "/" + bv + "/meta.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(text, indent=2, ensure_ascii=False))


def wirte_channel_video_complete(channel, bv):
    path = full_path(channel) + "/" + bv + "/complete"
    with open(path, "w", encoding="utf-8") as f:
        pass


def has_channel_video_complete(channel, bv):
    path = full_path(channel) + "/" + bv + "/complete"
    return os.path.isfile(path)


def download_audio(channel, bv):
    link = bilibili_link_prefix + str(bv)
    with yt_dlp.YoutubeDL(
        {
            "format": "worstaudio/worst",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": AUDIO_FORMAT,
                }
            ],
            "outtmpl": f'{full_path(channel)}/{str(bv)}/{str(bv)}',
        }
    ) as video:
        video.download(link)

def download_picture(channel, bv, pic_link):
    response = requests.get(pic_link, stream=True)
    with open(f'{full_path(channel)}/{str(bv)}/pic.jpg', 'wb') as f:
        f.write(response.content)


def load_channel_video_meta(channel, bv):
    path = full_path(channel) + "/" + bv + "/meta.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def timestamp_to_date(timestamp):
    return formatdate(timestamp, localtime=False, usegmt=True)


def get_channel_bilibili_link(uid, sid):
    return (
        "https://space.bilibili.com/"
        + str(uid)
        + "/channel/collectiondetail?sid="
        + str(sid)
        + "&amp;ctype=0"
    )


def write_to_rss_xml(channel, text):
    path = "rss/" + str(channel) + ".xml"
    if not os.path.exists("rss"):
        os.mkdir("rss")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def scan_channel_dir_to_generate_items_xml(channel):
    logger.info("===> start scan channel videos and generate item " + channel)
    items = []
    for video in load_channel_videos(channel):
        bv = video["bvid"]
        if os.path.isdir(full_path(channel) + "/" + bv):
            video_meta = load_channel_video_meta(channel, bv)
            audio_path = f'{full_path(channel)}/{bv}/{bv}.{AUDIO_FORMAT}'
            item = Template(item_template).substitute(
                {
                    "title": video_meta["title"],
                    "description": video_meta["desc"],
                    "image": f'{RSS_URL_PREFIX}{full_path(channel)}/{str(bv)}/pic.jpg',
                    "url": RSS_URL_PREFIX + audio_path,
                    "duration": video_meta["duration"],
                    "length": os.path.getsize(audio_path),
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
            "atom_link": RSS_URL_PREFIX + "rss/" + channel + ".xml",
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


load_global_config()
# 加载配置文件并创建channel文件夹，下载元信息
for channel in get_channel_list(load_config()):
    logger.info("===> channel load " + str(channel))
    channel_path = channel["sid"]
    channel_mkdir(channel_path)
    logger.info("===> start get channel series")
    series = get_channel_series(id=channel["sid"], uid=channel["uid"])
    channel_meta = get_channel_meta(series)
    wirte_channel_meta(channel_path, channel_meta)
    logger.info("===> wirte channel meta done")


# 循环每个channel
for channel in get_channel_sid_list(load_config()):
    logger.info("===> start load channel meta and get videos meta list")
    channel_meta = load_channel_meta(channel)
    videos = asyncio.run(get_channel_videos(channel_meta=channel_meta))
    wirte_channel_videos(channel, videos)
    logger.info("===> load channel meta and get videos meta list done")

# 循环每个channel
for channel in get_channel_sid_list(load_config()):
    logger.info("===> start download channel videos meta and audio" + channel)
    videos = load_channel_videos(channel)
    for video in videos:
        bv = video["bvid"]
        logger.info("===> start deal with BV: " + video["bvid"])
        # 判断如果存在complete文件则跳过
        if has_channel_video_complete(channel, bv):
            logger.info("===> BV: " + bv + "exist. skip.............")
            continue
        channel_videos_mkdir(channel, bv)
        # 写入每个视频的元信息
        video_info = asyncio.run(get_video_info(bv))
        del video_info["ugc_season"]
        wirte_channel_video_meta(channel=channel, bv=bv, text=video_info)
        logger.info("===> get video meta data done. start download audio")
        download_audio(channel, bv)
        download_picture(channel, bv, video_info['pic'])
        logger.info("===> download audio done. BV: " + bv)
        # 写入一个处理成功的标识
        wirte_channel_video_complete(channel, bv)

# 根据已经下载的文件生成播客格式的xml
for channel in get_channel_sid_list(load_config()):
    logger.info(
        "===> start generate rss xml file by channel info channel id: " + channel
    )
    write_to_rss_xml(channel, generate_channel_xml(channel))
    logger.info("===> generate rss xml file by channel info done. ")
