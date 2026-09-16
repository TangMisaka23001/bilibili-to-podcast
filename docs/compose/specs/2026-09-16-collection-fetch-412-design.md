# 2026-09-16 — 合集获取失效排查与「无头浏览器抓取」方案评估

## [S1] Problem

用户报告：**「根据链接获取视频合集的接口已经不可用」**，拟改用**无头浏览器打开合集页、提取元素**的方式替代。

本设计先验证两个隐含前提，再决定方案：

1. P1：现有「链接 → 合集视频列表」的接口链路确实已经失效。
2. P2：无头浏览器可以绕过现有的失效原因。

## [S2] 实测结论（关键事实）

测试环境：本仓库容器，2026-09-16，Python 3.12，`yt-dlp 2026.08.19`，`bilibili-api`（`vendor/bilibili-api-main`），Playwright `chromium-headless-shell 153.0.8010.12`。

### S2.1 接口 × 客户端矩阵

| 目标 | 客户端 | 结果 |
|---|---|---|
| 合集列表 `x/polymer/web-space/seasons_archives_list` | curl + 浏览器 UA | **200 / code=0**（连续 10 次全部成功） |
| 同上 | curl 不带 UA | HTTP 200，body **`code=-352`** |
| 同上 | bilibili-api 匿名 | **OK**，`sources.json` 全部 17 个源逐一验证通过 |
| 合集 meta `x/space/fav/season/list` | bilibili-api 匿名 | **OK** |
| 系列 meta `x/series/series` / 列表 `x/series/archives` | bilibili-api 匿名 | **OK**（样本 547718，564 项） |
| 视频详情 `x/web-interface/view` | bilibili-api 匿名 | **412（风控）** |
| 视频详情 | bilibili-api + `cookie` 文件凭据 | **200** |
| 合集页 HTML `space.bilibili.com/{uid}/lists/{sid}` | curl（含完整浏览器请求头） | **412** |
| 视频页 | yt-dlp 无 cookie | **412** |
| 视频页 | yt-dlp + `cookie` | **200** |

### S2.2 无头 Chromium 实测矩阵

| 场景 | 结果 |
|---|---|
| 带登录 cookie 打开合集页 | **2/2 成功**：主文档 200，拦截到 XHR `code=0, items=15` |
| 匿名打开合集页 | **1/3 成功**，2/3 主文档 412 且**未发出任何 XHR** |
| 匿名在页面上下文 `fetch` 合集列表 API | **code=0**（可用） |
| 匿名在页面上下文 `fetch` 视频详情 API | **412**（不可用） |
| 匿名浏览器引导出的 `buvid3/buvid4/bili_ticket` 回灌 Python 调详情 API | **仍 412** |
| 登录 cookie 回灌 Python 调详情 API | **200** |

资源开销：浏览器包 **114.3 MiB**（`chromium-headless-shell`）；进程启动 **0.56s**；从启动到拿到首份合集数据 **约 5–11s**（成功时）；常驻内存 **约 133 MB**。
`window.__INITIAL_STATE__` 为 `undefined`，页面无 SSR 初始状态可读；DOM 卡片类名可匹配（本次为 `.bili-video-card`），但仅渲染首屏 30 条，滚动未触发翻页。

### S2.3 日志证据

对仓库 `log`（4.5 MB）的统计：

- `状态码：412` 共 **70 次，全部位于视频详情 `fetch_video_info` 与 yt-dlp 下载路径**。
- 首次出现时间 **2026-09-03 01:12:36**（此前 0 次）——即风控是 9 月初新收紧的。
- 无 `Traceback`，无合集级中断记录 → **合集列表链路从未失败**。

## [S3] 结论：两个前提都不成立

- **P1 不成立**：合集 meta + 列表接口（season 与 series）当前全部返回 `code=0`，17 个源无一例外。失效的不是「获取合集」的接口，而是**视频详情与下载**链路。
- **P2 不成立**：无头浏览器**无法**绕过真正的失效点。匿名浏览器即使带着真实指纹，在页面上下文里请求 `x/web-interface/view` 依然是 412；只有**登录 Cookie（SESSDATA）** 能让该接口返回 200。
- 决定成败的变量是**凭据**，不是浏览器。无头浏览器仅在「带登录 cookie」时稳定，而那正是不需要它的场景。

**可行性结论：无头浏览器方案技术可行（能拿到合集列表），但作为本次问题的解法不可行（拿不到视频详情/下载），且匿名成功率约 33%，不适合作为主路径。**

## [S4] 真实根因

1. 2026-09-03 起 B 站对 `x/web-interface/view` 等接口收紧风控，匿名 IP 命中 412。
2. 本项目**只把 cookie 交给 yt-dlp**（`bilibili/audio.py::_resolve_cookie_file`），而 `bilibili/channel.py` 构造 `ChannelSeries` / `Video` 时**从未传入 `Credential`**，因此详情、封面等必经步骤走到 412。
3. 已有的 `fetch_video_info_with_fallback` 用 `videos.json` 缓存兜底，掩盖了故障，但没有解决新视频缺详情的问题（日志中 70 次 fallback）。

> 说明：若在其它网络环境仍能看到**合集列表**接口 412，属于 IP 维度风控差异，需补充该环境的原始报错后再评估；本设计以可复现的本机证据为准。

## [S5] 方案 A（推荐）：凭据化 API + 退避重试 + 显式降级

成本最低、命中根因、无新增重依赖。

### 改动点

1. 新增 `src/bilibili_podcast/bilibili/credential.py`
   - `load_credential() -> Credential | None`：从 `B2P_COOKIE_CONTENT` 或 `./cookie`（Netscape 格式）解析 `SESSDATA / bili_jct / buvid3 / buvid4 / DedeUserID`，构建 `bilibili_api.utils.network.Credential`，模块内缓存。
   - 与 `audio.py` 的 cookie 解析逻辑合并为单一来源，避免两处各写一遍。

2. `channel.py` 全部 API 调用注入凭据
   - `ChannelSeries(..., credential=cred)`（meta 与列表）
   - `video_api.Video(bvid=bv, credential=cred)`（详情）

3. `412 / -352` 退避重试
   - 对 `NetworkException` 中命中 412/-352 的请求做指数退避（如 1s→2s→4s，最多 3 次，带抖动）；仅对幂等 GET 生效。

4. 显式降级，不制造假成功
   - `fetch_video_info_with_fallback` 返回缓存记录前校验必需字段（如 `pic`），缺失则视为失败。
   - 缺少详情/封面时**不写 `complete`**，并记录 `partial` 状态，避免下轮被误跳过。

### 验收标准

- 在配置了有效 cookie 的前提下，`b2p-fetch` 对含新视频的合集：`状态码：412` 出现 **0 次**，新增视频全部生成 `meta.json` + `pic.jpg` + `complete`。
- 未配置 cookie 时，行为可预期：详情失败但不崩溃，日志明确提示「缺少凭据」，缓存兜底仍生效。
- 现有 `pytest`（77 passed）保持全绿。
- cookie 过期（当前 `SESSDATA` 到期 `2026-11-02`）后，日志中出现清晰的可操作告警，而非静默缺集。

## [S6] 方案 B（按需求给出设计）：无头浏览器抓取

**定位：仅在方案 A 失效（列表 API 也被 IP 级风控）时的兜底采集器，不作为默认路径。**

### 架构

```
ChannelRef ──► CollectionFetcher（接口抽象）
                 ├── ApiFetcher        # 方案 A，默认
                 └── BrowserFetcher    # 方案 B，兜底
```

- 复用现有 `ChannelRef` / `write_channel_videos` / `write_channel_meta`，下游零改动。
- 新增可选依赖 `playwright`，放入 `pyproject.toml` 的 `[project.optional-dependencies] browser`，不污染默认安装与现有 Docker 镜像。
- 每次进程只启一个浏览器实例，按 `ChannelRef` 复用 context；同步 API（`playwright.sync_api`）与现有同步代码风格一致。

### 提取策略（优先级从高到低）

1. **网络响应拦截（首选）**：监听 `page.on("response")`，捕获 `seasons_archives_list` / `x/series/archives` 的 JSON。
   - 优点：字段结构与 `bilibili-api` 返回一致（`archives[]` 含 `bvid/pubdate/pic/duration`），可直接喂现有 `write_channel_videos`；不受 DOM 类名变更影响。实测可稳定拿到 `code=0`。
2. **页面上下文 `fetch`（翻页首选）**：在 `page.evaluate` 中按 `page_num=1..N` 调列表 API，直到累计条数 ≥ 响应中的 `total`。实测匿名页内 `fetch` 列表接口返回 `code=0`，可绕开「页面滚动不触发翻页」的问题（实测滚动后 DOM 恒为 30 条）。
3. **DOM 抓取（最后手段）**：仅当 1、2 都失败时，读取卡片节点。注意类名可能变更、懒加载只渲染首屏，需滚动/点击「加载更多」并设上限。

### 分页与收敛

- 以接口返回的 `page.total` 为终止条件；无 `total` 时以「连续两页无新增」终止。
- 单源上限：页数 ≤ 50、总耗时 ≤ 120s、条目数 ≤ 5000，超出即中止并告警。

### 凭据与持久化

- 使用 `launch_persistent_context(user_data_dir=...)` 复用 `buvid3` 等指纹 cookie，减少每次重新引导。
- 登录态 cookie 仍从 `B2P_COOKIE_CONTENT` / `./cookie` 注入；**不要**把浏览器引导的匿名 cookie 当作解决方案（实测对详情接口无效）。
- `user_data_dir` 必须纳入 `.gitignore` 与 Docker volume 白名单。

### 部署成本

- 需在镜像/CI 中执行 `playwright install --with-deps chromium`：镜像 +约 114 MB 浏览器 + 系统库，CI 单次 +约 1 分钟。
- 常驻内存 +约 133 MB，冷启动 +约 0.6s，首数据 +5–11s。

### 失败模式

| 失败模式 | 缓解 |
|---|---|
| 匿名被风控（实测 2/3 失败） | 必须注入登录 cookie；失败即降级到方案 A/缓存 |
| DOM 类名变更 | 优先网络拦截，不用 DOM 作为主路径 |
| 页面改版 / 接口改名 | 拦截规则收敛到一个配置点，失败告警 |
| 无头被检测 | 只作为兜底，不作为 SLA 依赖；必要时 `headless=False` + xvfb |

### 明确不解决的问题

无头浏览器**不能**修复视频详情 412 与 yt-dlp 下载 412 —— 这两者实测在匿名浏览器环境下依旧失败。若详情链路失效，方案 B 也必须依赖方案 A 的凭据。

## [S7] 方案 C：浏览器仅用于刷新 Cookie

只有在需要**交互式登录**来续期 `SESSDATA` 时才有价值（当前 cookie 将于 `2026-11-02` 过期）。
匿名引导 `buvid3/bili_ticket` 已实测**不足以**解除详情接口 412，故不作为独立方案。
可作为方案 A 的一个可选运维脚本，而不是运行时依赖。

## [S8] 风险与回退

- cookie 属敏感凭据：仅经环境变量/本地文件注入，禁止写入仓库与日志（当前 `log` 中已出现完整 R2 密钥，属既有问题，建议另行清理）。
- 方案 A 全部为增量改动，回退方式：移除凭据注入即可恢复原行为。
- 方案 B 通过可选依赖隔离，不安装即不存在；回退等价于删除新增模块。

## [S9] 实施顺序与验收

1. **先做方案 A**（预计 < 100 行改动）：新增 `credential.py` → 注入 `channel.py` → 加重试与字段校验 → 补单测（凭据解析、412 退避、缓存字段校验）。
2. 观察 1–2 个 `b2p-fetch` 周期：确认 412 归零、新视频完整。
3. **仅当步骤 2 仍出现列表接口风控**时，再实施方案 B，并以可选依赖 + 开关（如 `B2P_FETCH_BACKEND=browser`）接入。
4. 验收：`pytest` 全绿；一轮 `start.sh` 后 `状态码：412` 计数为 0；RSS 中不出现缺 `pic`/缺 `meta` 的新条目。

## [S10] 补充发现与修复（2026-09-16 实盘排查 BV1ceYE6BEcd）

### S10.1 yt-dlp 会覆写 cookie 源文件（严重）

`yt-dlp` 的 `cookiefile` 是**读写**的：退出时会把 cookie jar 回写该文件。原先 `_resolve_cookie_file()` 在未设置 `B2P_COOKIE_CONTENT` 时直接返回 `./cookie`，导致每次 `b2p-fetch` 都可能重写登录 cookie。实盘观测到 `cookie` 从 2943 字节被改写成 525 字节，`SESSDATA/buvid3/DedeUserID` 全部丢失，进而使详情接口重新落到 412。

修复：`materialize_cookie_file()` 现在**始终**先复制到临时文件再交给 yt-dlp，源文件永不被写；无 cookie 时返回 `(None, False)`，让 yt-dlp 匿名运行而不是报文件缺失。测试 `test_materialize_cookie_file_protects_source_cookie` 锁定该行为。

> 结论：yt-dlp 版本与本问题无关。排查时本地 `yt-dlp 2026.08.19` 即 PyPI 最新版（`2026.8.19`），升级无法解决。

### S10.2 CDN 对象存在局部损坏区间

对 `BV1ceYE6BEcd`（时长 9401s，format 30216 约 74.12MiB）逐字节区间探测：

| Range 起始 | 结果 |
|---|---|
| 42,000,000 / 43,100,000 / 44,100,000 / 45,000,000 / 77,000,000 | `206` 正常 |
| **43,700,000 / 44,000,000** | **`503`，0 字节，多次重试复现** |

即 `upos-hz-mirrorakam.akamaized.net` 上该对象在 43.7–44.0MB 区间不可读。yt-dlp 顺序下载到该处即报 `Got error`，重试从同一区间附近恢复，永远无法越过。同一视频换用 format 30232/30280（不同对象）可正常下载。

修复：`download_audio()` 增加**音频格式降级链**，逐级尝试并在失败后清理残留分片：

```python
AUDIO_FORMAT_FALLBACKS = (
    "worstaudio/worst",                # ~66k
    "worstaudio[abr>=70]/bestaudio",   # ~84k
    "bestaudio/best",                  # ~147k
)
```

实盘验证：`worstaudio/worst` 在损坏区间失败 → 自动降级到 `bestaudio/best` → 下载 164.78MiB → 抽音完成，`ffprobe duration=9400.04s`（与视频时长一致）。

代价：降级产物体积显著增大（本例 m4a 约 172MB）。降级会打 `WARNING` 日志，便于事后甄别；中间档位 `30232`（约 94MiB）用于降低该代价。

### S10.3 残留分片清理

`_remove_stale_partials()` 在每次尝试前删除该 BV 的 `*.part` / `*.ytdl`。理由：残留分片会让 yt-dlp 从旧偏移续传，既可能落在损坏区间，也会污染换格式后的下载。由于 `complete` 标记已保证“已完成不再下载”，此处出现的分片必然来自失败尝试，删除是安全的。
