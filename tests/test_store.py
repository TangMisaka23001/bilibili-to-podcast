"""``store.py`` 的后端语义测试。

远端后端用 moto 模拟 S3；本地后端直接用 tmp_path。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from moto import mock_aws

from bilibili_podcast.store import (
    LocalStore,
    S3Path,
    S3Store,
    Stage,
    StoreError,
    as_root,
    build_store,
    is_remote,
    remove_tree,
    resolve_backend,
    resolve_root,
    stage_for,
    store_of,
)

BUCKET = "store-bucket"


@pytest.fixture
def s3_client():
    with mock_aws():
        import boto3

        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def s3_store(s3_client):
    return S3Store(BUCKET, s3_client)


@pytest.fixture
def r2_config():
    return {
        "R2": {
            "ACCESS_KEY": "ak",
            "SECRET_KEY": "sk",
            "ENDPOINT_URL": "https://example.invalid",
            "BUCKET_NAME": BUCKET,
        }
    }


# ---------------------------------------------------------------------------
# 本地后端
# ---------------------------------------------------------------------------


def test_local_store_root_is_plain_path(tmp_path):
    store = LocalStore(tmp_path)
    root = store.root()
    assert isinstance(root, Path)
    assert root == tmp_path
    assert not is_remote(root)


def test_local_stage_is_the_target_directory(tmp_path):
    target = tmp_path / "videos" / "BV1xx"
    store = LocalStore(tmp_path)
    with store.stage(target) as stage:
        assert stage.path == target
        assert target.is_dir()
        (stage.path / "a.txt").write_text("x")
        stage.put(stage.path / "a.txt")
    # 本地 put 是空操作，文件原地保留
    assert (target / "a.txt").read_text() == "x"


def test_local_stage_put_at_is_noop_for_same_file(tmp_path):
    target = tmp_path / "videos" / "BV1xx"
    store = LocalStore(tmp_path)
    with store.stage(target) as stage:
        local = stage.path / "a.txt"
        local.write_text("x")
        stage.put_at("a.txt", local)
    assert (target / "a.txt").read_text() == "x"


def test_stage_for_local_creates_directory(tmp_path):
    target = tmp_path / "out" / "BV1xx"
    with stage_for(target) as stage:
        assert stage.path == target
        assert target.is_dir()


def test_remove_tree_local(tmp_path):
    target = tmp_path / "a" / "b"
    target.mkdir(parents=True)
    (target / "f.txt").write_text("x")
    remove_tree(target)
    assert not (tmp_path / "a" / "b").exists()


def test_as_root_converts_str_to_path():
    assert as_root("output") == Path("output")
    assert as_root(Path("output")) == Path("output")


# ---------------------------------------------------------------------------
# 后端解析
# ---------------------------------------------------------------------------


def test_resolve_backend_defaults_to_local():
    assert resolve_backend({}) == "local"
    assert resolve_backend(None) == "local"


def test_resolve_backend_priority(monkeypatch):
    monkeypatch.setenv("B2P_STORAGE_BACKEND", "s3")
    assert resolve_backend({"storage": {"backend": "local"}}) == "s3"
    assert resolve_backend({"storage": {"backend": "local"}}, "local") == "local"
    monkeypatch.delenv("B2P_STORAGE_BACKEND")
    assert resolve_backend({"storage": {"backend": "s3"}}) == "s3"


def test_resolve_root_local_returns_path():
    root = resolve_root({}, "output")
    assert isinstance(root, Path)
    assert root == Path("output")


def test_resolve_root_remote_returns_s3_path(monkeypatch, r2_config):
    monkeypatch.setenv("B2P_STORAGE_BACKEND", "s3")
    root = resolve_root(r2_config, "output")
    assert isinstance(root, S3Path)
    assert str(root) == ""


def test_build_store_rejects_unknown_backend(r2_config):
    with pytest.raises(StoreError):
        build_store(r2_config, backend="ftp")


def test_build_store_requires_r2_credentials():
    with pytest.raises(StoreError) as excinfo:
        build_store({"R2": {}}, backend="s3")
    assert "ACCESS_KEY" in str(excinfo.value)


def test_store_of_local_and_remote(monkeypatch, r2_config):
    assert isinstance(store_of(Path("output")), LocalStore)
    monkeypatch.setenv("B2P_STORAGE_BACKEND", "s3")
    store = store_of("output", r2_config)
    assert isinstance(store, S3Store)
    assert store.bucket == BUCKET


# ---------------------------------------------------------------------------
# 远端路径语义
# ---------------------------------------------------------------------------


def test_s3_path_name_parts(s3_store):
    path = s3_store.root() / "bilibili-season" / "9057559" / "BV1xx" / "BV1xx.m4a"
    assert path.name == "BV1xx.m4a"
    assert path.stem == "BV1xx"
    assert path.suffix == ".m4a"
    assert str(path.parent) == "bilibili-season/9057559/BV1xx"
    assert str(path.parent.parent) == "bilibili-season/9057559"


def test_s3_path_with_suffix(s3_store):
    path = s3_store.root() / "a" / "BV1xx.m4a"
    assert str(path.with_suffix(".tmp.m4a")) == "a/BV1xx.tmp.m4a"


def test_s3_path_root_parent_is_root(s3_store):
    assert str(s3_store.root().parent) == ""


def test_s3_path_truediv_with_empty_root(s3_store):
    assert str(s3_store.root() / "index.html") == "index.html"


# ---------------------------------------------------------------------------
# 远端读写
# ---------------------------------------------------------------------------


def test_s3_write_and_read_text(s3_store):
    path = s3_store.root() / "meta.json"
    path.write_text(json.dumps({"a": 1}))
    assert path.read_text() == '{"a": 1}'
    assert path.exists()
    assert path.is_file()
    assert not path.is_dir()


def test_s3_write_and_read_bytes(s3_store):
    path = s3_store.root() / "a.bin"
    path.write_bytes(b"hello")
    assert path.read_bytes() == b"hello"
    assert path.stat().st_size == 5


def test_s3_touch_creates_empty_object(s3_store):
    path = s3_store.root() / "complete"
    path.touch()
    assert path.exists()
    assert path.stat().st_size == 0


def test_s3_missing_object_raises_file_not_found(s3_store):
    path = s3_store.root() / "nope.json"
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        path.read_text()
    with pytest.raises(FileNotFoundError):
        path.stat()


def test_s3_unlink(s3_store):
    path = s3_store.root() / "a.txt"
    path.write_text("x")
    path.unlink()
    assert not path.exists()
    with pytest.raises(FileNotFoundError):
        path.unlink()
    path.unlink(missing_ok=True)


def test_s3_content_type_uses_storage_rules(s3_store, s3_client):
    (s3_store.root() / "chapters.json").write_text("{}")
    head = s3_client.head_object(Bucket=BUCKET, Key="chapters.json")
    assert head["ContentType"] == "application/json+chapters"

    (s3_store.root() / "a.m4a").write_bytes(b"x")
    head = s3_client.head_object(Bucket=BUCKET, Key="a.m4a")
    assert head["ContentType"] == "audio/mp4"
    assert "immutable" in head["CacheControl"]


def test_s3_mkdir_is_noop(s3_store):
    (s3_store.root() / "a" / "b").mkdir(parents=True, exist_ok=True)
    assert not (s3_store.root() / "a" / "b").exists()


# ---------------------------------------------------------------------------
# 目录列举
# ---------------------------------------------------------------------------


def _seed(store: S3Store) -> None:
    root = store.root()
    (root / "bilibili-season" / "1" / "BV1" / "BV1.m4a").write_bytes(b"a")
    (root / "bilibili-season" / "1" / "BV1" / "meta.json").write_text("{}")
    (root / "bilibili-season" / "1" / "BV1" / "complete").touch()
    (root / "bilibili-season" / "2" / "BV2" / "BV2.m4a").write_bytes(b"bb")
    (root / "bilibili-season" / "2" / "videos.json").write_text("[]")
    (root / "bilibili-new" / "9" / "BV3" / "BV3.m4a").write_bytes(b"ccc")
    (root / "rss" / "season" / "1.xml").write_text("<rss/>")
    (root / "index.html").write_text("<html/>")


def test_s3_children_lists_files_and_dirs(s3_store):
    _seed(s3_store)
    names = sorted(p.name for p in (s3_store.root() / "bilibili-season" / "1").iterdir())
    assert names == ["BV1"]
    names = sorted(p.name for p in (s3_store.root() / "bilibili-season" / "2").iterdir())
    assert names == ["BV2", "videos.json"]


def test_s3_children_of_root(s3_store):
    _seed(s3_store)
    names = sorted(p.name for p in s3_store.root().iterdir())
    assert names == ["bilibili-new", "bilibili-season", "index.html", "rss"]


def test_s3_is_dir_for_prefix(s3_store):
    _seed(s3_store)
    assert (s3_store.root() / "bilibili-season").is_dir()
    assert not (s3_store.root() / "bilibili-season").is_file()
    assert (s3_store.root() / "index.html").is_file()


def test_s3_exists_for_dir_without_object(s3_store):
    _seed(s3_store)
    assert (s3_store.root() / "rss" / "season").exists()
    assert (s3_store.root() / "rss" / "season").is_dir()


def test_s3_glob_matches_two_levels(s3_store):
    _seed(s3_store)
    root = s3_store.root() / "bilibili-season"
    matched = sorted(str(p) for p in root.glob("*/*"))
    assert matched == [
        "bilibili-season/1/BV1",
        "bilibili-season/2/BV2",
        "bilibili-season/2/videos.json",
    ]


def test_s3_glob_single_level(s3_store):
    _seed(s3_store)
    matched = sorted(str(p) for p in (s3_store.root() / "bilibili-season").glob("*"))
    assert matched == ["bilibili-season/1", "bilibili-season/2"]


def test_s3_glob_star_does_not_cross_slash(s3_store):
    _seed(s3_store)
    matched = sorted(str(p) for p in (s3_store.root() / "bilibili-season").glob("*/BV1/*"))
    assert matched == [
        "bilibili-season/1/BV1/BV1.m4a",
        "bilibili-season/1/BV1/complete",
        "bilibili-season/1/BV1/meta.json",
    ]


def test_s3_list_objects_returns_sizes_in_store_key_space(s3_store):
    """``list_objects`` 的 key 与 ``object_size`` 同属一个 key 空间。"""
    _seed(s3_store)
    objects = s3_store.list_objects("bilibili-season")
    assert objects["bilibili-season/1/BV1/BV1.m4a"] == 1
    assert objects["bilibili-season/2/BV2/BV2.m4a"] == 2


def test_s3_list_objects_prewarms_size_cache(s3_store, s3_client):
    _seed(s3_store)
    calls = []
    original = s3_client.head_object

    def counting_head(**kwargs):
        calls.append(kwargs["Key"])
        return original(**kwargs)

    s3_client.head_object = counting_head
    s3_store.list_objects("bilibili-season")
    s3_store.object_size("bilibili-season/1/BV1/BV1.m4a")
    assert "bilibili-season/1/BV1/BV1.m4a" not in calls


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------


def test_s3_delete_prefix(s3_store):
    _seed(s3_store)
    removed = s3_store.delete_prefix("bilibili-season/1")
    assert removed == 3
    assert not (s3_store.root() / "bilibili-season" / "1" / "BV1" / "BV1.m4a").exists()
    assert (s3_store.root() / "bilibili-season" / "2" / "BV2" / "BV2.m4a").exists()


def test_remove_tree_remote(s3_store):
    _seed(s3_store)
    remove_tree(s3_store.root() / "bilibili-season" / "1")
    assert not (s3_store.root() / "bilibili-season" / "1" / "BV1" / "BV1.m4a").exists()
    assert (s3_store.root() / "bilibili-season" / "2" / "BV2" / "BV2.m4a").exists()


def test_s3_cache_invalidated_after_write(s3_store):
    path = s3_store.root() / "a" / "b.txt"
    path.write_text("x")
    assert s3_store.list_objects("a") == {"a/b.txt": 1}
    path.write_text("yy")
    assert s3_store.list_objects("a") == {"a/b.txt": 2}


def test_s3_list_objects_with_store_prefix_uses_relative_keys(s3_client):
    store = S3Store(BUCKET, s3_client, prefix="podcast")
    (store.root() / "a" / "b.txt").write_text("x")
    assert store.list_objects("a") == {"a/b.txt": 1}


# ---------------------------------------------------------------------------
# 远端暂存区
# ---------------------------------------------------------------------------


def test_remote_stage_is_temp_dir_and_uploads(s3_store):
    target = s3_store.root() / "bilibili-new" / "1" / "BV1" / "BV1.m4a"
    with stage_for(target) as stage:
        assert stage.path != target
        assert stage.path.is_dir()
        assert str(stage.path).startswith(os.sep) or ":" in str(stage.path)
        local = stage.path / "BV1.m4a"
        local.write_bytes(b"audio")
        stage.put(local)
        assert (s3_store.root() / "bilibili-new" / "1" / "BV1" / "BV1.m4a").exists()
    assert not stage.path.exists()


def test_remote_stage_put_at_and_fetch(s3_store):
    target = s3_store.root() / "videos" / "BV1"
    with stage_for(target) as stage:
        local = stage.path / "meta.json"
        local.write_text('{"bvid": "BV1"}')
        stage.put_at("meta.json", local)
        assert (s3_store.root() / "videos" / "BV1" / "meta.json").read_text() == '{"bvid": "BV1"}'

        pulled = stage.fetch("meta.json", s3_store.root() / "videos" / "BV1" / "meta.json")
        assert pulled.read_text() == '{"bvid": "BV1"}'


def test_stage_put_file_sets_content_type(s3_store, s3_client):
    target = s3_store.root() / "a.m4a"
    local = s3_store.root().parent  # 仅为占位
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "b2p-store-test.bin"
    tmp.write_bytes(b"audio")
    try:
        s3_store.put_file(target, tmp)
        head = s3_client.head_object(Bucket=BUCKET, Key="a.m4a")
        assert head["ContentType"] == "audio/mp4"
    finally:
        tmp.unlink(missing_ok=True)
    assert local is not None


# ---------------------------------------------------------------------------
# 前缀（prefix）模式
# ---------------------------------------------------------------------------


def test_s3_store_with_prefix_uses_prefixed_keys(s3_store, s3_client):
    store = S3Store(BUCKET, s3_client, prefix="podcast")
    (store.root() / "index.html").write_text("<html/>")
    assert (s3_client.head_object(Bucket=BUCKET, Key="podcast/index.html")["ContentLength"] == 7)
    assert store.list_objects("") == {"index.html": 7}


def test_s3_store_with_prefix_delete_prefix_only_touches_its_scope(s3_client):
    store = S3Store(BUCKET, s3_client, prefix="podcast")
    other = S3Store(BUCKET, s3_client)
    (store.root() / "a.txt").write_text("x")
    (other.root() / "b.txt").write_text("y")
    store.delete_prefix("a.txt")
    assert not (store.root() / "a.txt").exists()
    assert (other.root() / "b.txt").exists()
