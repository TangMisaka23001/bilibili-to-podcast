import pytest

from bilibili_podcast.extract_url import parse_url


def test_parse_url_season_extracts_uid_sid_and_type():
    url = "https://space.bilibili.com/391930545/lists/598034?type=season"

    result = parse_url(url)

    assert result.uid == "391930545"
    assert result.sid == "598034"
    assert result.type == "season"
    assert result.url == url


def test_parse_url_series_extracts_uid_sid_and_type():
    url = "https://space.bilibili.com/3546729368520811/lists/4281748?type=series"

    result = parse_url(url)

    assert result.uid == "3546729368520811"
    assert result.sid == "4281748"
    assert result.type == "series"


def test_parse_url_missing_type_raises():
    url = "https://space.bilibili.com/391930545/lists/598034"

    with pytest.raises(ValueError, match="type"):
        parse_url(url)


def test_parse_url_invalid_type_raises():
    url = "https://space.bilibili.com/391930545/lists/598034?type=bogus"

    with pytest.raises(ValueError, match="bogus"):
        parse_url(url)


def test_parse_url_non_bilibili_domain_raises():
    url = "https://example.com/391930545/lists/598034?type=season"

    with pytest.raises(ValueError):
        parse_url(url)
