# 2026-06-14 — 重构为可安装 Python 包

## [S1] Problem

`bilibili-to-podcast` 当前以"散落脚本集合"形式组织（`src/*.py` 平铺，依赖隐式 cwd=`src/`）。两类症状：

**症状 A — 当前真实 bug**：在 cwd=`src/` 下执行 `python bilibili_season.py` 时，
`bilibili_audio_download.py` 第 3 行 `from config import ...` 触发 `config.py`
顶层 import，新加的 `from src.tools.config_loader import load_active_config`
失败（`ModuleNotFoundError: No module named 'src'`），因为 `src/` 不在
`sys.path` 里。

**症状 B — 项目级反模式**（memory 已记录为 "Refactor target"）：
1. `bilibili_season.py` / `bilibili_series.py` 一导入就跑全流程（import 副作用）
2. `config.py` 模块顶层 `_load_global_config()` 自动读 yaml（无法复用、无法测试）
3. `bilibili_season.py` 和 `bilibili_series.py` 154 行近乎重复（差异只在
   `ChannelSeriesType` 枚举、字段名、`complete` 检查路径）
4. `wirte_channel_meta` 等 `wirte_*` 拼写错误（4 处）
5. `config.py` 顶层 `global FETCH_RECENT_N_VIDEOS` 是 dead code（已删除但留痕）
6. `logger.py` 的 `../log` 硬编码相对路径
7. 整个项目**不可** `pip install`，只能用裸 `python xxx.py`

## [S2] Solution overview

把 `src/tools/*` 和 `src/*.py` 整合为一个可 `pip install -e .` 安装的 Python 包
`bilibili_podcast`，配套 `pyproject.toml` 和 `b2p-*` console scripts。

**核心变化**：
1. 所有 module 改为绝对包导入（`from bilibili_podcast.config import ...`）
2. 所有 CLI 入口加 `if __name__ == "__main__":` 守卫，导入不再跑 pipeline
3. season/series 重复代码统一到 `bilibili_podcast/bilibili/channel.py` 的
   `fetch_all(configs)`
4. 提供 `b2p-prune` / `b2p-sync` / `b2p-fetch` / `b2p-cron` CLI 命令

## [S3] Target layout

```
.
├── pyproject.toml              # 新增：包定义 + entry points
├── README.md                   # 更新：推荐 pip install + b2p-* 命令
├── config.yaml.example         # 移到项目根
├── docs/...
├── src/
│   └── bilibili_podcast/       # 新包（替换原 src/tools/ + src/*.py 平铺）
│       ├── __init__.py
│       ├── py.typed
│       ├── config.py           # 不再 module-level load
│       ├── config_loader.py    # from .extract_url import ...
│       ├── extract_url.py
│       ├── xml_template.py
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── prune.py        # → b2p-prune
│       │   ├── sync.py         # → b2p-sync
│       │   ├── fetch.py        # → b2p-fetch
│       │   └── cron.py         # → b2p-cron
│       ├── bilibili/
│       │   ├── __init__.py
│       │   ├── channel.py      # ChannelType + fetch_all()
│       │   ├── audio.py        # yt_dlp 抽取
│       │   └── meta.py         # 元数据读写
│       ├── output.py           # 本地 output 目录抽象
│       ├── storage.py          # S3 sync (覆盖原 upload_r2)
│       ├── rss.py              # RSS XML 生成
│       └── logger.py           # 不再 module-level 加 handler
└── tests/
    ├── conftest.py             # 用 fixture 注入 tmp_path，不再 sys.path hack
    └── ...                     # 现有 32 测试改 import 路径
```

## [S4] Entry points (pyproject.toml)

```toml
[project]
name = "bilibili-podcast"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "boto3>=1.34",
    "botocore>=1.34",
    "PyYAML>=6.0",
    "yt-dlp>=2025.10",
    "schedule>=1.2",
    "bilibili-api-python @ git+https://github.com/Nemo2011/bilibili-api.git@dev",
]

[project.scripts]
b2p-prune = "bilibili_podcast.cli.prune:main"
b2p-sync  = "bilibili_podcast.cli.sync:main"
b2p-fetch = "bilibili_podcast.cli.fetch:main"
b2p-cron  = "bilibili_podcast.cli.cron:main"
```

## [S5] season/series 合并

旧 `src/bilibili_season.py` (154 行) + `src/bilibili_series.py` (153 行) 合并为
单一 `bilibili_podcast/bilibili/channel.py`：

```python
class ChannelType(str, Enum):
    SEASON = "season"
    SERIES = "series"

@dataclass(frozen=True)
class ChannelRef:
    type: ChannelType
    uid: str
    sid: str

def fetch_all(refs: list[ChannelRef], output_root: Path = Path("output")) -> None:
    for ref in refs:
        _fetch_one(ref, output_root)def _fetch_one(ref: ChannelRef, output_root: Path) -> None:
    # 统一的：拉 meta → 翻页拉视频 → 逐个下载 → 写 complete
    # season vs series 差异在 type → API type 字段映射处集中处理
    ...
```

调用方 `cli/fetch.py`：

```python
def main(argv=None) -> int:
    args = parse_args(argv)
    config = load_active_config(args.config)
    refs = [
        ChannelRef(type=ChannelType.SEASON, uid=c["uid"], sid=c["sid"])
        for c in config["season"]
    ] + [
        ChannelRef(type=ChannelType.SERIES, uid=c["uid"], sid=c["sid"])
        for c in config["series"]
    ]
    fetch_all(refs, output_root=args.output_root)
    return 0
```

## [S6] config.py 改造

- 删模块顶层 `_load_global_config()` 调用
- 不再有 module-level `config` 全局 dict
- 提供：
  ```python
  @dataclass(frozen=True)
  class Config:
      rss_url_prefix: str
      port: int
      r2: R2Config
      season: list[ChannelRef]
      series: list[ChannelRef]

  def load_config(path: Path | str) -> Config: ...
  ```
- 现有 `from config import RSS_URL_PREFIX, ...` 改为
  `from bilibili_podcast.config import load_config` 后调用
- CLI 用 `--config` 参数显式传路径，不再依赖 cwd

## [S7] 反模式清理清单

| 反模式 | 修复 |
|--------|------|
| `from config import ...` (隐式 cwd) | `from bilibili_podcast.config import ...` |
| Module-level `for channel in ...: download(...)` | 移到 `main()`，加 `if __name__ == "__main__"` |
| Module-level `_load_global_config()` | 删除，改为 `load_config(path)` 函数 |
| `wirte_*` 拼写 (4 处) | 全部 `write_*` |
| `FETCH_RECENT_N_VIDEOS` dead code | 已删（确认无引用） |
| `../log` / `../config.yaml` 硬编码相对路径 | `Path.cwd()` 派生或 CLI 参数 |
| Logger module-level handler | 改为 `get_logger(name)` 工厂函数 |

## [S8] Tests

**保留**：现有 32 个测试覆盖的核心行为（解析、派生、CLI、sync、prune、config_loader）
**改 import 路径**：`from src.tools.X` → `from bilibili_podcast.X` 或 `from bilibili_podcast.cli.X`
**新增**：
- `tests/test_channel.py`：season/series 合并后的 `fetch_all` 行为（mock bilibili_api）
- `tests/test_install.py`：smoke test，验证 `pip install -e .` 后 `b2p-fetch --help` 可用
- `tests/test_config.py`：新 `Config` dataclass 字段映射

## [S9] 风险与回退

| 风险 | 缓解 |
|------|------|
| Dockerfile `start_up.py` 不存在（pre-existing） | 不在本次范围；README 提示改用 `b2p-cron` |
| Docker Hub tag 自动构建镜像 | tag 推送前本地 `pip install -e . && b2p-fetch --help` 验证 |
| 32 测试改 import 路径可能漏改 | 每个 commit 后跑 `pytest`，红了立即停 |
| 用户 Cron 调用 `python cron.py` 失败 | README 明确 `b2p-cron` 是新入口 |
| config.yaml 路径变化破坏用户 | README 明确新默认路径，旧 `../config.yaml` 通过 `--config` 兼容 |

**回退**：每个 commit 是独立可回滚的。如果 C5（console_scripts 接入）出问题，
仍可回退到 C4 状态（包已成形但脚本仍为 `python -m`）。

## [S10] Commit 拆分（按风险递增）

| # | Commit | 内容 | 验证 |
|---|--------|------|------|
| C1 | fix: config.py lazy import 解 ModuleNotFoundError | 最小修复 | 32 测试通过 + `python bilibili_season.py` cwd=`src/` 不报错 |
| C2 | feat: 引入 pyproject.toml + src/bilibili_podcast/ 包骨架 | 空骨架包 | `pip install -e .` 成功 + 32 测试改 import 后通过 |
| C3 | refactor: src/tools/* 迁移到 bilibili_podcast/ 子包 | 改名 + 改 import | 32 测试通过 |
| C4 | refactor: 合并 bilibili_season.py + bilibili_series.py 为 channel.fetch_all | 拆 season/series 重复 | 新测试 + 32 测试通过 |
| C5 | feat: 引入 b2p-* console_scripts + start.sh/cron.py 更新 | 新 CLI 命令 | `b2p-fetch --help` 可用 + 32 测试通过 |
| C6 | chore: 删除 dead code、修 wirte_* 拼写、修 logger.py 路径 | 反模式清理 | 32 测试通过 + smoke test |

每个 commit 前必须跑 `pytest`，红了就停。
