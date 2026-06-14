from pathlib import Path

import pytest
import yaml

from src.tools.prune_output import prune, PruneResult


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    (out / "bilibili-season" / "598034").mkdir(parents=True)
    (out / "bilibili-season" / "598034" / "BV1").mkdir()
    (out / "bilibili-season" / "598034" / "BV1" / "complete").touch()
    (out / "bilibili-season" / "999").mkdir(parents=True)
    (out / "bilibili-season" / "999" / "BV-old").mkdir()
    (out / "bilibili-season" / "999" / "BV-old" / "complete").touch()
    (out / "bilibili-series" / "4281748").mkdir(parents=True)
    (out / "bilibili-series" / "4281748" / "BV2.m4a").touch()
    (out / "bilibili-series" / "12345").mkdir(parents=True)
    (out / "rss").mkdir(parents=True, exist_ok=True)
    (out / "rss" / "598034.xml").write_text("<rss/>")
    return out


def _config(tmp_path: Path, **kw) -> Path:
    cfg = {"RSS_URL_PREFIX": "https://x/", **kw}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    return p


def test_prune_deletes_sids_not_in_legacy_season(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        season=[{"uid": "1", "sid": "598034"}],
        series=[{"uid": "2", "sid": "4281748"}],
    )

    result = prune(cfg, output_root=str(workspace))

    assert result.deleted_season == ["999"]
    assert result.deleted_series == ["12345"]
    assert (workspace / "bilibili-season" / "598034").exists()
    assert not (workspace / "bilibili-season" / "999").exists()


def test_prune_keeps_rss_dir_untouched(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        season=[{"uid": "1", "sid": "598034"}],
        series=[{"uid": "2", "sid": "4281748"}],
    )

    prune(cfg, output_root=str(workspace))

    assert (workspace / "rss" / "598034.xml").exists()


def test_prune_does_not_recurse_into_kept_dirs(workspace, tmp_path):
    cfg = _config(tmp_path, season=[{"uid": "1", "sid": "598034"}])
    (workspace / "bilibili-season" / "598034" / "BV1" / "BV1.m4a").touch()

    prune(cfg, output_root=str(workspace))

    assert (workspace / "bilibili-season" / "598034" / "BV1" / "BV1.m4a").exists()


def test_prune_handles_sources_field(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        sources=[
            "https://space.bilibili.com/1/lists/598034?type=season",
            "https://space.bilibili.com/2/lists/4281748?type=series",
        ],
    )

    result = prune(cfg, output_root=str(workspace))

    assert result.deleted_season == ["999"]
    assert result.deleted_series == ["12345"]


def test_prune_errors_when_sources_and_legacy_both_present(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        sources=["https://space.bilibili.com/1/lists/598034?type=season"],
        season=[{"uid": "9", "sid": "99"}],
    )

    with pytest.raises(Exception, match="sources"):
        prune(cfg, output_root=str(workspace))


def test_prune_with_no_sids_in_config_deletes_all(workspace, tmp_path):
    cfg = _config(tmp_path)  # no season/series

    result = prune(cfg, output_root=str(workspace))

    assert sorted(result.deleted_season) == ["598034", "999"]
    assert sorted(result.deleted_series) == ["12345", "4281748"]
    assert not (workspace / "bilibili-season" / "598034").exists()
    assert not (workspace / "bilibili-season" / "999").exists()
    assert not (workspace / "bilibili-series" / "4281748").exists()


def test_prune_returns_empty_when_everything_matches(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        season=[{"uid": "1", "sid": "598034"}, {"uid": "9", "sid": "999"}],
        series=[{"uid": "2", "sid": "4281748"}, {"uid": "3", "sid": "12345"}],
    )

    result = prune(cfg, output_root=str(workspace))

    assert result.deleted_season == []
    assert result.deleted_series == []
