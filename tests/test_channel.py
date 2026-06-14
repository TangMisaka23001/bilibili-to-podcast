from bilibili_podcast.bilibili.channel import ChannelType, ChannelRef, fetch_all


def test_channel_type_values():
    assert ChannelType.SEASON.value == "season"
    assert ChannelType.SERIES.value == "series"


def test_channel_ref_is_hashable():
    a = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    b = ChannelRef(type=ChannelType.SEASON, uid="1", sid="10")
    assert a == b
    assert hash(a) == hash(b)


def test_fetch_all_with_empty_list_does_nothing(tmp_path):
    fetch_all([], output_root=tmp_path)
    assert list(tmp_path.iterdir()) == []
