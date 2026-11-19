"""存储抽象层：把「产物放在哪」从业务逻辑里剥离出来。

设计要点
--------
业务代码只依赖两个协议：

``StorePath``
    路径句柄。本地后端直接返回 ``pathlib.Path``（本地模式行为与改造前逐字节
    一致），对象存储后端返回 ``S3Path``。两者支持同一套操作：``/``、``name``、
    ``parent``、``suffix``、``stem``、``exists()``、``is_file()``、``is_dir()``、
    ``stat().st_size``、``read_text()``、``write_text()``、``read_bytes()``、
    ``write_bytes()``、``touch()``、``mkdir()``、``iterdir()``、``glob()``、
    ``unlink()``、``with_suffix()``。

``Store``
    后端能力。负责根路径、本地工作目录（yt-dlp / ffmpeg 必须拿到真实本地
    文件，所以下载与转码永远在本地临时目录完成，处理完再上传回对象存储），
    以及按前缀删除。

不变式：产物在对象存储里的 key == 相对 ``output`` 根目录的路径；
公开 URL == ``RSS_URL_PREFIX`` + 相对路径。

根路径即后端
------------
所有对外入口（``rss.generate``、``cli.fetch`` 等）接受 ``str | Path |
S3Path``：

- 传 ``str`` / ``Path`` → 本地后端，语义与改造前完全一致（现有测试零改动）。
- 传 ``S3Path`` → 对象存储后端。

CLI 用 :func:`resolve_root` 构造根句柄，后端由 ``storage.backend`` 配置项
（或 ``B2P_STORAGE_BACKEND`` 环境变量、``--backend`` 参数）决定。
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

from bilibili_podcast.logger import get_logger
from bilibili_podcast.storage import (
    CHAPTERS_CONTENT_TYPE,
    _cache_control,
    _content_type,
    make_s3_client,
)

logger = get_logger(__name__)

_LOCAL_BACKENDS = ("local", "file", "fs", "disk")
_REMOTE_BACKENDS = ("s3", "r2", "object", "objectstorage")


class StoreError(RuntimeError):
    """存储后端不可用或配置缺失。"""


class Stat:
    """``os.stat_result`` 的最小替身；业务代码只用 ``st_size``。"""

    __slots__ = ("st_size",)

    def __init__(self, st_size: int) -> None:
        self.st_size = int(st_size)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Stat(st_size={self.st_size})"


# ---------------------------------------------------------------------------
# 路径句柄
# ---------------------------------------------------------------------------


class StorePath:
    """远端路径句柄的公共接口。

    本地后端不使用这个类，直接返回 ``pathlib.Path``——它天然满足这里列出的
    全部方法。业务代码只使用本接口，因此同一套代码可在两种后端上运行。
    """

    name: str = ""
    suffix: str = ""
    stem: str = ""

    def __truediv__(self, other: str) -> "StorePath":
        raise NotImplementedError

    def exists(self) -> bool:
        raise NotImplementedError

    def is_file(self) -> bool:
        raise NotImplementedError

    def is_dir(self) -> bool:
        raise NotImplementedError

    def stat(self) -> Stat:
        raise NotImplementedError

    def read_text(self, encoding: str = "utf-8") -> str:
        raise NotImplementedError

    def read_bytes(self) -> bytes:
        raise NotImplementedError

    def write_text(self, data: str, encoding: str = "utf-8") -> int:
        raise NotImplementedError

    def write_bytes(self, data: bytes) -> int:
        raise NotImplementedError

    def touch(self) -> None:
        raise NotImplementedError

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        raise NotImplementedError

    def iterdir(self) -> list["StorePath"]:
        raise NotImplementedError

    def glob(self, pattern: str) -> list["StorePath"]:
        raise NotImplementedError

    def unlink(self, missing_ok: bool = False) -> None:
        raise NotImplementedError

    def with_suffix(self, suffix: str) -> "StorePath":
        raise NotImplementedError


class S3Path(StorePath):
    """对象存储里的某个 key。"""

    __slots__ = ("store", "key", "_is_dir")

    def __init__(self, store: "S3Store", key: str, is_dir: bool | None = None) -> None:
        self.store = store
        self.key = key.strip("/")
        self._is_dir = is_dir

    # -- 路径语义 -------------------------------------------------------
    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1] if "/" in self.key else self.key

    @property
    def parent(self) -> "S3Path":
        if "/" not in self.key:
            return S3Path(self.store, "", is_dir=True)
        return S3Path(self.store, self.key.rsplit("/", 1)[0], is_dir=True)

    @property
    def suffix(self) -> str:
        name = self.name
        dot = name.rfind(".")
        return name[dot:] if dot > 0 else ""

    @property
    def stem(self) -> str:
        name = self.name
        suffix = self.suffix
        return name[: -len(suffix)] if suffix else name

    def __truediv__(self, other: str) -> "S3Path":
        other = str(other)
        key = f"{self.key}/{other}" if self.key else other
        return S3Path(self.store, key)

    def __str__(self) -> str:
        return self.key

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"S3Path({self.key!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, S3Path):
            return NotImplemented
        return self.key == other.key and self.store is other.store

    def __hash__(self) -> int:
        return hash((id(self.store), self.key))

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, S3Path):
            return self.key < other.key
        return NotImplemented

    # -- 读写 -----------------------------------------------------------
    def exists(self) -> bool:
        if self.store.object_size(self.key) is not None:
            return True
        return self.store.has_children(self.key)

    def is_file(self) -> bool:
        # ``_is_dir`` 表示「已知是目录」：True=目录，False=文件，None=未知。
        if self._is_dir is True:
            return False
        return self.store.object_size(self.key) is not None

    def is_dir(self) -> bool:
        if self._is_dir is True:
            return True
        if self._is_dir is False:
            return False
        return self.store.has_children(self.key)

    def stat(self) -> Stat:
        size = self.store.object_size(self.key)
        if size is None:
            raise FileNotFoundError(self.key)
        return Stat(size)

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.store.get_bytes(self.key).decode(encoding)

    def read_bytes(self) -> bytes:
        return self.store.get_bytes(self.key)
    def write_text(self, data: str, encoding: str = "utf-8") -> int:
        payload = data.encode(encoding)
        self.store.put_bytes(self.key, payload)
        return len(payload)

    def write_bytes(self, data: bytes) -> int:
        self.store.put_bytes(self.key, data)
        return len(data)

    def touch(self) -> None:
        self.store.put_bytes(self.key, b"")

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        # 对象存储没有目录概念；空操作即可，保持接口一致。
        return None

    def iterdir(self) -> list["S3Path"]:
        return self.store.children(self.key)

    def glob(self, pattern: str) -> list["S3Path"]:
        return self.store.glob(self.key, pattern)

    def unlink(self, missing_ok: bool = False) -> None:
        if self.store.object_size(self.key) is None:
            if missing_ok:
                return
            raise FileNotFoundError(self.key)
        self.store.delete(self.key)

    def with_suffix(self, suffix: str) -> "S3Path":
        name = self.name
        dot = name.rfind(".")
        base = name[:dot] if dot > 0 else name
        return self.parent / f"{base}{suffix}"


# ---------------------------------------------------------------------------
# 后端
# ---------------------------------------------------------------------------


class Stage:
    """一次本地工作目录：给 yt-dlp / ffmpeg 用，处理完再写回后端。

    本地后端下 ``path`` 就是真实目录、``put`` 是空操作，行为与改造前一致。
    """

    def __init__(self, store: "Store", target: Any, local_dir: Path, cleanup: bool) -> None:
        self.store = store
        self.target = target
        self._dir = local_dir
        self._cleanup = cleanup

    @property
    def path(self) -> Path:
        """本地工作目录。"""
        return self._dir

    def put(self, local_file: Path) -> None:
        """把本地文件写回目标目录（文件名保持不变）。"""
        self.put_at(Path(local_file).name, local_file)

    def put_at(self, name: str, local_file: Path) -> None:
        """把本地文件写回目标目录下的 ``name``。"""
        self.store.put_file(self.target / name, Path(local_file))

    def fetch(self, name: str, remote: Any) -> Path:
        """把远端对象拉到本地工作目录，返回本地路径。"""
        local = self._dir / name
        self.store.fetch_to(remote, local)
        return local

    def __enter__(self) -> "Stage":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._cleanup:
            shutil.rmtree(self._dir, ignore_errors=True)


class Store(ABC):
    """存储后端。"""

    @abstractmethod
    def root(self) -> Any:
        """根路径句柄（本地是 ``Path``，远端是 ``S3Path``）。"""

    @abstractmethod
    def stage(self, target: Any) -> Stage:
        """为目标路径准备工作目录。"""

    @abstractmethod
    def put_file(self, target: Any, local_file: Path) -> None:
        """把本地文件写入目标位置。"""

    @abstractmethod
    def fetch_to(self, target: Any, local_file: Path) -> None:
        """把目标位置的对象拉到本地文件。"""


class LocalStore(Store):
    """本地目录后端；``root()`` 返回真实 ``Path``，语义与改造前完全一致。"""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def root(self) -> Path:
        return self._root

    def stage(self, target: Any) -> Stage:
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        return Stage(self, path, path, cleanup=False)

    def put_file(self, target: Any, local_file: Path) -> None:
        local = Path(local_file)
        dest = Path(target)
        if local.resolve() == dest.resolve():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, dest)

    def fetch_to(self, target: Any, local_file: Path) -> None:
        src = Path(target)
        dest = Path(local_file)
        if src.resolve() == dest.resolve():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


class S3Store(Store):
    """对象存储后端（S3 / R2）。"""

    def __init__(self, bucket: str, client: Any, prefix: str = "") -> None:
        self.bucket = bucket
        self.client = client
        self.prefix = prefix.strip("/")
        self._sizes: dict[str, int] = {}
        self._missing: set[str] = set()
        self._listings: dict[str, dict[str, int]] = {}

    # -- key 映射 -------------------------------------------------------
    def _abs_key(self, key: str) -> str:
        key = key.strip("/")
        if not self.prefix:
            return key
        return f"{self.prefix}/{key}" if key else self.prefix

    def _rel_key(self, abs_key: str) -> str:
        if not self.prefix:
            return abs_key
        if abs_key == self.prefix:
            return ""
        if abs_key.startswith(self.prefix + "/"):
            return abs_key[len(self.prefix) + 1 :]
        return abs_key

    # -- 元数据 ---------------------------------------------------------
    def object_size(self, key: str) -> int | None:
        """返回对象字节数；不存在返回 None。"""
        key = key.strip("/")
        if not key:
            # 桶根不是对象，head_object 会因为 Key 为空而报参数错误。
            return None
        if key in self._sizes:
            return self._sizes[key]
        if key in self._missing:
            return None
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._abs_key(key))
        except Exception as exc:
            if _is_missing(exc):
                self._missing.add(key)
                return None
            raise
        size = int(head["ContentLength"])
        self._sizes[key] = size
        return size

    def has_children(self, key: str) -> bool:
        """前缀下是否还有对象（即是不是一个「目录」）。"""
        return bool(self.list_objects(key))

    def list_objects(self, prefix: str = "") -> dict[str, int]:
        """列出前缀下的全部对象：key（相对 store prefix）-> size。

        返回的 key 与 :meth:`object_size` 的 key 空间一致（即相对 store
        prefix，而非相对查询前缀），这样删除与列举都能直接复用同一套
        key 映射，不必再做一次前缀换算。
        """
        prefix = prefix.strip("/")
        cached = self._listings.get(prefix)
        if cached is not None:
            return cached
        if prefix:
            abs_prefix = self._abs_key(prefix) + "/"
        else:
            abs_prefix = (self._abs_key("") + "/") if self.prefix else ""
        found: dict[str, int] = {}
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": abs_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            for item in resp.get("Contents", []):
                rel = self._rel_key(item["Key"])
                size = int(item["Size"])
                found[rel] = size
                self._sizes[rel] = size
                self._missing.discard(rel)
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        self._listings[prefix] = found
        return found

    def children(self, prefix: str = "") -> list[S3Path]:
        """列出直接子项（文件与「目录」），按 key 排序。"""
        prefix = prefix.strip("/")
        objects = self.list_objects(prefix)
        base = f"{prefix}/" if prefix else ""
        files: set[str] = set()
        dirs: set[str] = set()
        for key in objects:
            if base and not key.startswith(base):
                continue
            rest = key[len(base) :]
            if not rest:
                continue
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
            else:
                files.add(rest)
        out = [S3Path(self, f"{base}{name}", is_dir=False) for name in sorted(files)]
        out.extend(S3Path(self, f"{base}{name}", is_dir=True) for name in sorted(dirs))
        return out
    def glob(self, prefix: str, pattern: str) -> list[S3Path]:
        """按 ``pathlib`` 语义匹配前缀下的相对路径。

        ``*`` 不跨目录分隔符，因此 ``*/*`` 恰好匹配两级路径——这与
        ``Path.glob("*/*")`` 一致（``fnmatch`` 不具备该语义，故不复用）。
        """
        prefix = prefix.strip("/")
        base = f"{prefix}/" if prefix else ""
        objects = self.list_objects(prefix)
        pat_parts = [part for part in pattern.split("/") if part]
        files: set[str] = set()
        dirs: set[str] = set()
        for key in objects:
            if base and not key.startswith(base):
                continue
            rest = key[len(base) :]
            if not rest:
                continue
            parts = rest.split("/")
            files.add("/".join(parts))
            for i in range(1, len(parts)):
                dirs.add("/".join(parts[:i]))
        matched: list[str] = []
        for rel in files:
            if _glob_match(pat_parts, rel.split("/")):
                matched.append(rel)
        for rel in dirs:
            if _glob_match(pat_parts, rel.split("/")):
                matched.append(rel)
        matched.sort()
        return [
            S3Path(self, f"{base}{rel}", is_dir=(rel in dirs))
            for rel in matched
        ]

    # -- 读写 -----------------------------------------------------------
    def get_bytes(self, key: str) -> bytes:
        key = key.strip("/")
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._abs_key(key))
        except Exception as exc:
            if _is_missing(exc):
                self._missing.add(key)
                raise FileNotFoundError(key) from exc
            raise
        return obj["Body"].read()

    def put_bytes(self, key: str, data: bytes) -> None:
        key = key.strip("/")
        self.client.put_object(
            Bucket=self.bucket,
            Key=self._abs_key(key),
            Body=data,
            ContentType=_content_type_for(key),
            CacheControl=_cache_control_for(key),
        )
        self._sizes[key] = len(data)
        self._missing.discard(key)
        self._invalidate_listings(key)

    def delete(self, key: str) -> None:
        key = key.strip("/")
        self.client.delete_object(Bucket=self.bucket, Key=self._abs_key(key))
        self._sizes.pop(key, None)
        self._missing.add(key)
        self._invalidate_listings(key)

    def delete_prefix(self, prefix: str) -> int:
        """删除前缀下的全部对象，返回删除数量。

        ``prefix`` 既可以是一个「目录前缀」，也可以正好是某个对象的 key
        （后者用于删除单个文件）。两种情况都会一并处理。
        """
        prefix = prefix.strip("/")
        removed = 0
        if self.object_size(prefix) is not None:
            self.client.delete_object(Bucket=self.bucket, Key=self._abs_key(prefix))
            self._sizes.pop(prefix, None)
            self._missing.add(prefix)
            removed += 1
        objects = self.list_objects(prefix)
        base = f"{prefix}/" if prefix else ""
        for key in sorted(objects):
            if base and not key.startswith(base):
                continue
            self.client.delete_object(Bucket=self.bucket, Key=self._abs_key(key))
            self._sizes.pop(key, None)
            self._missing.add(key)
            removed += 1
        self._invalidate_listings(prefix)
        return removed

    def _invalidate_listings(self, key: str) -> None:
        key = key.strip("/")
        for prefix in list(self._listings):
            if key == prefix or key.startswith(f"{prefix}/") or prefix.startswith(f"{key}/"):
                self._listings.pop(prefix, None)

    # -- Store 接口 -----------------------------------------------------
    def root(self) -> S3Path:
        return S3Path(self, "", is_dir=True)

    def stage(self, target: Any) -> Stage:
        tmp = Path(tempfile.mkdtemp(prefix="b2p-stage-"))
        return Stage(self, target, tmp, cleanup=True)

    def put_file(self, target: Any, local_file: Path) -> None:
        key = str(target).strip("/")
        self.client.upload_file(
            str(local_file),
            self.bucket,
            self._abs_key(key),
            ExtraArgs={
                "ContentType": _content_type_for(key),
                "CacheControl": _cache_control_for(key),
            },
        )
        self._sizes.pop(key, None)
        self._missing.discard(key)
        self._invalidate_listings(key)

    def fetch_to(self, target: Any, local_file: Path) -> None:
        key = str(target).strip("/")
        dest = Path(local_file)
        dest.parent.mkdir(parents=True, exist_ok=True)
        obj = self.client.get_object(Bucket=self.bucket, Key=self._abs_key(key))
        body = obj["Body"]
        try:
            with dest.open("wb") as handle:
                for chunk in body.iter_chunks(chunk_size=1 << 20):
                    if chunk:
                        handle.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _glob_match(pattern_parts: list[str], path_parts: list[str]) -> bool:
    """``pathlib`` 风格的 glob 匹配：``*`` 不跨目录分隔符。"""
    if not pattern_parts:
        return not path_parts
    head, rest = pattern_parts[0], pattern_parts[1:]
    if head == "**":
        for i in range(len(path_parts) + 1):
            if _glob_match(rest, path_parts[i:]):
                return True
        return False
    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], head):
        return False
    return _glob_match(rest, path_parts[1:])


def _is_missing(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") or {}
        if str(error.get("Code", "")) in ("404", "NoSuchKey", "NotFound"):
            return True
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if status == 404:
            return True
    text = str(exc)
    return "404" in text or "Not Found" in text


def is_remote(path: Any) -> bool:
    """该路径句柄是否位于对象存储。"""
    return isinstance(path, S3Path)


def _content_type_for(key: str) -> str:
    """按 key 推导 Content-Type。

    ``storage._content_type`` 只把 ``/chapters.json``（带前导斜杠的完整
    后缀）认作章节文件；对象存储里的 key 没有前导斜杠，因此这里补一条
    兜底规则，避免顶层 ``chapters.json`` 落成普通 JSON。
    """
    if key.endswith("chapters.json"):
        return CHAPTERS_CONTENT_TYPE
    return _content_type(key)


def _cache_control_for(key: str) -> str:
    """按 key 推导 Cache-Control。"""
    return _cache_control(key)


def as_root(value: Any) -> Any:
    """把字符串根路径转成 ``Path``；``Path`` / ``S3Path`` 原样返回。

    对外入口统一接受 ``str | Path | S3Path``：``str`` 一律按本地目录处理，
    这样既有调用方（传 ``"output"``）行为不变。
    """
    if isinstance(value, str):
        return Path(value)
    return value


def remove_tree(target: Any) -> None:
    """删除一个「目录」（本地递归删，远端按前缀删）。"""
    if isinstance(target, S3Path):
        target.store.delete_prefix(target.key)
        return
    shutil.rmtree(target)


def stage_for(target: Any) -> Stage:
    """为目标路径准备工作目录（本地 → 就是该目录；远端 → 临时目录）。"""
    if isinstance(target, S3Path):
        return target.store.stage(target)
    path = Path(target)
    path.mkdir(parents=True, exist_ok=True)
    return Stage(LocalStore(path), path, path, cleanup=False)


def resolve_backend(config: dict | None, backend: str | None = None) -> str:
    """决定使用哪个后端：显式参数 > 环境变量 > 配置 > local。"""
    if backend:
        return backend.strip().lower()
    env = os.environ.get("B2P_STORAGE_BACKEND")
    if env:
        return env.strip().lower()
    storage_cfg = (config or {}).get("storage") or {}
    chosen = storage_cfg.get("backend") or "local"
    return str(chosen).strip().lower()


def build_store(
    config: dict | None,
    *,
    backend: str | None = None,
    local_root: Path | str = "output",
) -> Store:
    """按配置构造存储后端。"""
    config = config or {}
    chosen = resolve_backend(config, backend)
    if chosen in _LOCAL_BACKENDS:
        return LocalStore(Path(local_root))
    if chosen in _REMOTE_BACKENDS:
        r2 = config.get("R2") or {}
        missing = [
            key
            for key in ("ACCESS_KEY", "SECRET_KEY", "ENDPOINT_URL", "BUCKET_NAME")
            if not r2.get(key)
        ]
        if missing:
            raise StoreError(f"R2 配置缺失: {', '.join(missing)}")
        client = make_s3_client(r2["ACCESS_KEY"], r2["SECRET_KEY"], r2["ENDPOINT_URL"])
        prefix = str((config.get("storage") or {}).get("prefix") or "")
        return S3Store(r2["BUCKET_NAME"], client, prefix)
    raise StoreError(f"未知的存储后端: {chosen}")


def resolve_root(
    config: dict | None,
    output_root: Path | str = "output",
    backend: str | None = None,
) -> Any:
    """构造根路径句柄：本地返回 ``Path``，对象存储返回 ``S3Path``。"""
    chosen = resolve_backend(config, backend)
    if chosen in _REMOTE_BACKENDS:
        return build_store(config, backend=chosen, local_root=output_root).root()
    return Path(output_root)


def store_of(root: Any, config: dict | None = None) -> Store:
    """拿到与根句柄对应的后端。

    ``S3Path`` 自带后端；``str`` / ``Path`` 则按配置里的 backend 决定
    （默认本地），这样 ``store_of(root)`` 与 ``resolve_root(root)`` 的语义
    保持一致。
    """
    if isinstance(root, S3Path):
        return root.store
    if isinstance(root, (Path, str)):
        backend = resolve_backend(config)
        if backend in _REMOTE_BACKENDS:
            return build_store(config, backend=backend, local_root=root)
        return LocalStore(Path(root))
    raise StoreError(f"无法识别的根目录: {root!r}")


__all__ = [
    "LocalStore",
    "S3Path",
    "S3Store",
    "Stage",
    "Stat",
    "Store",
    "StoreError",
    "StorePath",
    "as_root",
    "build_store",
    "is_remote",
    "remove_tree",
    "resolve_backend",
    "resolve_root",
    "stage_for",
    "store_of",
]
