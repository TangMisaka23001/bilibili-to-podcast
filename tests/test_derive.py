import pytest

from src.tools.extract_url import parse_sources, to_legacy_config


def test_parse_sources_returns_one_per_url():
    urls = [
        "https://space.bilibili.com/391930545/lists/598034?type=season",
        "https://space.bilibili.com/3546729368520811/lists/4281748?type=series",
    ]

    result = parse_sources(urls)

    assert len(result) == 2
    assert result[0].uid == "391930545"
    assert result[1].type == "series"


def test_parse_sources_aborts_on_first_invalid():
    urls = [
        "https://space.bilibili.com/391930545/lists/598034?type=season",
        "https://example.com/1/lists/2?type=season",  # invalid host
        "https://space.bilibili.com/3/lists/4?type=series",
    ]

    with pytest.raises(ValueError, match="example.com"):
        parse_sources(urls)


def test_parse_sources_empty_list_returns_empty():
    assert parse_sources([]) == []


def test_to_legacy_config_partitions_by_type():
    sources = parse_sources([
        "https://space.bilibili.com/1/lists/10?type=season",
        "https://space.bilibili.com/2/lists/20?type=series",
        "https://space.bilibili.com/3/lists/30?type=season",
    ])

    legacy = to_legacy_config(sources)

    assert legacy == {
        "season": [{"uid": "1", "sid": "10"}, {"uid": "3", "sid": "30"}],
        "series": [{"uid": "2", "sid": "20"}],
    }


def test_to_legacy_config_empty_input_yields_empty_lists():
    assert to_legacy_config([]) == {"season": [], "series": []}
