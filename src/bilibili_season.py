import os
import json
import asyncio
from logger import logger
from bilibili_api import video as video_api
from bilibili_api.channel_series import ChannelOrder, ChannelSeriesType, ChannelSeries
from bilibili_audio_download import download_channel_audio as download_audio, download_channel_picture as download_picture
from config import (
    config,
    season_base_path,
)
from file import has_season_video_complete


def full_path(path):
    return season_base_path + str(path)


def channel_mkdir(channel):
    path = full_path(channel)
    if not os.path.exists(path):
        os.makedirs(path)


def get_channel_list():
    return config["season"]


def get_channel_sid_list():
    return [str(channel["sid"]) for channel in get_channel_list()]


def get_channel_series(id, uid):
    return ChannelSeries(
        id_=id, uid=uid, type_=ChannelSeriesType.SEASON
    )


def get_channel_meta(series):
    return asyncio.run(series.get_meta())


def wirte_channel_meta(channel, text):
    path = full_path(channel) + "/meta.json"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(text, indent=2, ensure_ascii=False))


def load_channel_meta(channel):
    file_path = full_path(channel) + "/meta.json"
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


async def get_channel_videos(channel_meta):
    series = get_channel_series(id=channel_meta["id"], uid=channel_meta["mid"])
    # 合集有分页，这里需要解开分页返回全部视频信息
    pn = 1
    result = []
    while True:
        # page_videos = await series.get_videos(sort=ChannelOrder.CHANGE, pn=pn)
        page_videos = await series.get_videos(sort=ChannelOrder.DEFAULT, pn=pn)
        result += page_videos["archives"]
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


# 加载配置文件并创建channel文件夹，下载元信息
for channel in get_channel_list():
    logger.info("===> channel load " + str(channel))
    channel_path = channel["sid"]
    channel_mkdir(channel_path)
    logger.info("===> start get channel series")
    series = get_channel_series(id=channel["sid"], uid=channel["uid"])
    channel_meta = get_channel_meta(series)
    wirte_channel_meta(channel_path, channel_meta)
    logger.info("===> wirte channel meta done")


# 循环每个channel
for channel in get_channel_sid_list():
    logger.info("===> start load channel meta and get videos meta list")
    channel_meta = load_channel_meta(channel)
    videos = asyncio.run(get_channel_videos(channel_meta=channel_meta))
    wirte_channel_videos(channel, videos)
    logger.info("===> load channel meta and get videos meta list done")

# 循环每个channel
for channel in get_channel_sid_list():
    logger.info("===> start download channel videos meta and audio" + channel)
    videos = load_channel_videos(channel)
    for video in videos:
        bv = video["bvid"]
        logger.info("===> start deal with BV: " + video["bvid"])
        # # 判断如果存在complete文件则跳过
        # if has_channel_video_complete(channel, bv):
        #     logger.info("===> BV: " + bv + "exist. skip.............")
        #     continue
        # 判断如果存在complete文件则跳过
        if has_season_video_complete(channel, bv):
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
