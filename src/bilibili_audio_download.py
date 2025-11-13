import yt_dlp
import requests
from config import AUDIO_FORMAT, bilibili_link_prefix, season_base_path, series_base_path

def download_channel_audio(channel, bv):
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
            "outtmpl": f'{season_base_path + str(channel)}/{str(bv)}/{str(bv)}',
            "cookiefile": "cookie"
        }
    ) as video:
        video.download(link)
        
def download_series_audio(channel, bv):
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
            "outtmpl": f'{series_base_path + str(channel)}/{str(bv)}/{str(bv)}',
            "cookiefile": "cookie"
        }
    ) as video:
        video.download(link)
        
def download_channel_picture(channel, bv, pic_link):
    response = requests.get(pic_link, stream=True)
    with open(f'{season_base_path + str(channel)}/{str(bv)}/pic.jpg', 'wb') as f:
        f.write(response.content)
        
def download_series_picture(channel, bv, pic_link):
    response = requests.get(pic_link, stream=True)
    with open(f'{series_base_path + str(channel)}/{str(bv)}/pic.jpg', 'wb') as f:
        f.write(response.content)

