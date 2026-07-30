import json
from pathlib import Path

import pytest
import yaml

from bilibili_podcast.cli.prune import prune, PruneResult


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


def _config(tmp_path: Path, sources: list[str]) -> Path:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"RSS_URL_PREFIX": "https://x/"}, allow_unicode=True, sort_keys=False)
    )
    (tmp_path / "sources.json").write_text(
        json.dumps({"all": sources, "new": []}, ensure_ascii=False)
    )
    return tmp_path / "config.yaml"


def test_prune_deletes_sids_not_in_legacy_season(workspace, tmp_path):
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
    assert (workspace / "bilibili-season" / "598034").exists()
    assert not (workspace / "bilibili-season" / "999").exists()


def test_prune_keeps_rss_dir_untouched(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        sources=[
            "https://space.bilibili.com/1/lists/598034?type=season",
            "https://space.bilibili.com/2/lists/4281748?type=series",
        ],
    )

    prune(cfg, output_root=str(workspace))

    assert (workspace / "rss" / "598034.xml").exists()


def test_prune_does_not_recurse_into_kept_dirs(workspace, tmp_path):
    cfg = _config(tmp_path, sources=["https://space.bilibili.com/1/lists/598034?type=season"])
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


def test_prune_with_no_sids_in_config_deletes_all(workspace, tmp_path):
    cfg = _config(tmp_path, sources=[])

    result = prune(cfg, output_root=str(workspace))

    assert sorted(result.deleted_season) == ["598034", "999"]
    assert sorted(result.deleted_series) == ["12345", "4281748"]
    assert not (workspace / "bilibili-season" / "598034").exists()
    assert not (workspace / "bilibili-season" / "999").exists()
    assert not (workspace / "bilibili-series" / "4281748").exists()


def test_prune_returns_empty_when_everything_matches(workspace, tmp_path):
    cfg = _config(
        tmp_path,
        sources=[
            "https://space.bilibili.com/1/lists/598034?type=season",
            "https://space.bilibili.com/9/lists/999?type=season",
            "https://space.bilibili.com/2/lists/4281748?type=series",
            "https://space.bilibili.com/3/lists/12345?type=series",
        ],
    )

    result = prune(cfg, output_root=str(workspace))

    assert result.deleted_season == []
    assert result.deleted_series == []
    assert result.deleted_new == []


# --- new-array pruning (bilibili-new/{sid}/) ---

def _write_sources_object(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(data, ensure_ascii=False))
    return p


def _workspace_with_new(tmp_path: Path) -> Path:
    """workspace + bilibili-new/{sid}/ entries."""
    out = tmp_path / "output"
    (out / "bilibili-new" / "100").mkdir(parents=True)
    (out / "bilibili-new" / "100" / "BV1").touch()
    (out / "bilibili-new" / "200").mkdir(parents=True)
    (out / "bilibili-new" / "300").mkdir(parents=True)
    (out / "bilibili-new" / "300" / "BV1").touch()
    (out / "bilibili-new" / "300" / "BV1.m4a").touch()
    return out


def test_prune_removes_new_sids_not_in_config(workspace, tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"RSS_URL_PREFIX": "https://x/"}, allow_unicode=True, sort_keys=False)
    )
    (tmp_path / "sources.json").write_text(json.dumps({
        "all": [],
        "new": [
            "https://space.bilibili.com/1/lists/100?type=season",   # keep
            "https://space.bilibili.com/2/lists/300?type=season",   # keep
            # 200 should be pruned
        ],
    }, ensure_ascii=False))

    out = _workspace_with_new(tmp_path)
    result = prune(tmp_path / "config.yaml", output_root=str(out))

    assert result.deleted_new == ["200"]
    assert (out / "bilibili-new" / "100").exists()
    assert not (out / "bilibili-new" / "200").exists()
    assert (out / "bilibili-new" / "300").exists()


def test_prune_does_not_touch_season_when_only_new_changed(workspace, tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"RSS_URL_PREFIX": "https://x/"}, allow_unicode=True, sort_keys=False)
    )
    (tmp_path / "sources.json").write_text(json.dumps({
        "all": [
            "https://space.bilibili.com/1/lists/598034?type=season",
            "https://space.bilibili.com/2/lists/4281748?type=series",
        ],
        "new": [],
    }, ensure_ascii=False))

    out = workspace  # has bilibili-season/{598034, 999} and bilibili-series/{4281748, 12345}
    result = prune(tmp_path / "config.yaml", output_root=str(out))

    # season + series untouched, no new entries either
    assert result.deleted_season == ["999"]
    assert result.deleted_series == ["12345"]
    assert result.deleted_new == []


def test_prune_with_new_dry_run_does_not_delete(tmp_path):
    from bilibili_podcast.cli.prune import main as prune_main
    import io, contextlib

    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"RSS_URL_PREFIX": "https://x/"}, allow_unicode=True, sort_keys=False)
    )
    (tmp_path / "sources.json").write_text(json.dumps({
        "all": [],
        "new": ["https://space.bilibili.com/1/lists/100?type=season"],
    }, ensure_ascii=False))
    out = _workspace_with_new(tmp_path)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = prune_main(["--config", str(tmp_path / "config.yaml"), "--output-root", str(out), "--dry-run"])

    assert rc == 0
    assert "would delete (new): ['200', '300']" in buf.getvalue()
    # nothing actually deleted
    assert (out / "bilibili-new" / "200").exists()
    assert (out / "bilibili-new" / "300").exists()
