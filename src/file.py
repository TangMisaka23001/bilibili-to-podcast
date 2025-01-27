from upload_r2 import object_exists, get_object


def has_season_video_complete(channel, bv):
    return object_exists(f"bilibili-season/{channel}/{bv}/complete")

def has_series_video_complete(channel, bv):
    return object_exists(f"bilibili-series/{channel}/{bv}/complete")

def load_season_videos(channel):
    return get_object(f"bilibili-season/{channel}/videos.json")
    
def load_season_video_meta(channel, bv):
    return get_object(f"bilibili-season/{channel}/{bv}/meta.json")
