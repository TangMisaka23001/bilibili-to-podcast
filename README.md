# bilibili-to-podcast

把 B 站**合集（season）/ 系列（series）**自动转成标准播客 RSS：抓元数据 → 抽音频 → 生成 XML → 镜像到对象存储（Cloudflare R2 或任意 S3 兼容服务）→ 生成订阅首页。

订阅地址生成后可直接粘进 Apple Podcasts、Pocket Casts、AntennaPod 等客户端。

```
sources.json (合集 URL)
        │
        ├─ b2p-prune      清理已从 sources.json 移除的合集目录
        ├─ b2p-fetch      B站 API 取元数据 → yt-dlp + ffmpeg 抽音频
        ├─ b2p-summarize  取 B站 AI 总结 → description 素材 + 章节文件（仅最新视频流）
        ├─ b2p-rss        生成 rss/season/{sid}.xml、rss/series/{sid}.xml、rss/new.xml
        ├─ b2p-gen-index  生成 output/index.html（订阅首页）
        ├─ b2p-sync       把 output/ 镜像到 R2（含 Content-Type / Cache-Control）
        └─ b2p-size       打印本轮磁盘占用
```

### 两种存储模式

| 模式 | 后端 | 本地磁盘 | 适用场景 |
|---|---|---|---|
| `local`（默认） | 本地目录 `output/` | 保留全部产物 | 本地调试、离线分析 |
| `s3` / `r2` | 对象存储（Cloudflare R2 或任意 S3 兼容服务） | **只保留临时工作目录**，跑完即删 | 常驻机器、磁盘紧张的服务器 |

直连模式下所有文件读写直接打对象存储：`b2p-fetch` 把音频下载到临时目录处理完就上传，
`b2p-rss` 直接把 XML 写进桶，`b2p-gen-index` 直接写 `index.html`。本地不再留 `output/`，
`b2p-sync` 变成空操作（没有「本地→远端」这一步了）。

## 特性

- 支持 B 站两种合集类型：`season`（合集）与 `series`（系列）
- 音频默认抽成 `m4a`（最低码率，单集通常几 MB～几十 MB）
- **两种存储后端**：`local` 保留本地 `output/`；`s3`/`r2` 直连对象存储，本地只留临时工作目录（跑完即删），省掉「下载到本地再同步上传」的两段式
- **响度归一化**：新下载的音频自动用 ffmpeg `loudnorm` 归一化到 −16 LUFS（Apple Podcasts 标准），车上收听不用再手动调音量
- **幂等可重跑**：每个视频目录下有 `complete` 标记，已成功的不会重复下载
- **可从对象存储恢复**：`b2p-pull` 把桶里的产物拉回本地 `output/`，不重下音频；配合 `complete` 标记，`b2p-fetch` 不会重复抓取
- **最新视频聚合流**：`rss/new.xml` 跨多个合集取各自最新 N 个，按发布时间倒序合并
- **AI 总结与章节**：`rss/new.xml` 的每集描述带 B站官方 AI 总结 + 按时间轴的分章摘要，并输出 Podcasting 2.0 `podcast:chapters`（Apple Podcasts / Pocket Casts / Overcast 可点章节跳转）
- 同步是**真镜像**：本地有变化就重传、远端多出来的就删除，不会出现「改了但线上没变」
- 上传自动设置 `ContentType` 与 `Cache-Control`，静态首页与音频都能被客户端/浏览器正确处理
- 24 小时定时调度内置（`b2p-cron`），也可交给系统 cron / systemd timer

## 快速开始

### 0. 系统依赖

| 依赖 | 说明 |
|---|---|
| Python **3.11+** | 使用 `datetime.UTC` 等 3.11 特性 |
| **ffmpeg** | yt-dlp 抽音频用；`brew install ffmpeg` / `apt install ffmpeg` |
| **B站登录 Cookie** | 视频详情接口与下载在匿名状态下会返回 412，必须登录态 |

### 1. 解压 vendored 依赖

仓库不直接包含 `vendor/`（B站的 `bilibili-api` fork 快照），以根目录的 `vendor.tar.gz` 分发，**首次安装前必须先解压**：

```bash
tar -xzf vendor.tar.gz          # 生成 vendor/bilibili-api-main/
```

### 2. 安装

```bash
python -m venv .venv && source .venv/bin/activate

# 先装 vendored 的 bilibili-api fork（不在 pyproject 的 dependencies 里：
# PEP 621 无法表达相对路径，写绝对路径会把仓库锁死在某个克隆位置）
pip install -e ./vendor/bilibili-api-main

# 再装本项目与开发依赖
pip install -e ".[dev]"
```

跳过第二步会报 `ModuleNotFoundError: No module named 'bilibili_api'`。

### 3. 配置要追踪的合集：`sources.json`

```json
{
  "all": [
    "https://space.bilibili.com/391930545/lists/8722727?type=season"
  ],
  "new": [
    "https://space.bilibili.com/23947287/lists/665?type=season"
  ]
}
```

| 字段 | 行为 |
|---|---|
| `all` | 长期追踪：抓合集内**全部**视频，每个合集一条独立 RSS |
| `new` | 只观察：每个合集只抓**最新 N 个**（默认 5），不生成独立 RSS，只在 `rss/new.xml` 里出现 |

URL 必须形如 `https://space.bilibili.com/{uid}/lists/{sid}?type=season|series`，缺 `type` 或缺数字 `uid/sid` 会直接报错。文件缺失会启动失败。

### 4. 配置对外域名与对象存储：`config.yaml`

```yaml
RSS_URL_PREFIX: https://podcast.example.com/   # 必须以 / 结尾；等于桶的公开域名

# 存储后端：local（默认，本地 output/）或 s3 / r2（直连对象存储）
storage:
  backend: r2
  prefix: ""            # 可选：桶内的子前缀，留空表示用桶根

R2:
  ACCESS_KEY: xxxxxxxx
  SECRET_KEY: xxxxxxxx
  ENDPOINT_URL: https://<account-id>.r2.cloudflarestorage.com
  BUCKET_NAME: bilibili-podcast

# 音频响度归一化（可选，默认开启）
audio:
  normalize_loudness: true
```

> `config.yaml` 与 `cookie` 都在 `.gitignore` 里，不要提交。
> `b2p-sync` **只从 `config.yaml` 的 `R2` 段读凭据**，环境变量不会生效。
> 想放到别处：`export B2P_CONFIG_PATH=/path/to/config.yaml`（`sources.json` 需与它同目录）。
> `storage.backend` 也可以用环境变量覆盖：`export B2P_STORAGE_BACKEND=s3`；
> 每条命令还有 `--backend local|s3|r2` 参数，优先级高于环境变量与配置。
> 响度归一化可用 `export B2P_NORMALIZE_LOUDNESS=0` 临时关闭。


### 5. 放 Cookie

二选一：

```bash
# A. 环境变量（适合 CI）
export B2P_COOKIE_CONTENT="$(cat cookies.txt)"

# B. 仓库根目录的 cookie 文件（Netscape 格式，浏览器插件导出即可）
cp ~/Downloads/cookies.txt ./cookie
```

### 6. 跑一遍

```bash
bash start.sh
```

看到 `uploaded=N deleted=N skipped=N failed=0` 且桶里出现 `rss/`、`index.html` 即成功；`failed` 非 0 时命令以非零码退出。

## 命令一览

| 命令 | 作用 | 常用参数 |
|---|---|---|
| `b2p-prune` | 删除已不在 `sources.json` 中的合集目录，以及它们留下的孤儿 feed | `--dry-run`、`--output-root`、`--backend` |
| `b2p-fetch` | 拉合集元数据 + 逐个视频下载音频 | `--new-top-n`（默认 5）、`--output-root`、`--backend` |
| `b2p-pull` | 把桶里的产物拉回本地 `output/`（不重下音频） | `--prefix`（可重复）、`--workers`（默认 8）、`--output-root` |
| `b2p-summarize` | 抓 B站 AI 总结 + 章节（只处理最新视频流） | `--force`、`--retry-days`、`--sleep`、`--backend` |
| `b2p-rss` | 生成 RSS XML | `--new-top-n`、`--output-root`、`--backend` |
| `b2p-gen-index` | 生成订阅首页 HTML | `--output`（默认 `<output-root>/index.html`）、`--backend` |
| `b2p-sync` | 把 `output/` 镜像到桶（远端模式下为空操作） | `--output-root`、`--backend` |
| `b2p-size` | 打印 `output/`（或桶）各子目录体积 | `--output-root`、`--backend` |
| `b2p-cron` | 每 24h 跑一轮 prune→fetch→summarize→rss→gen-index→sync | `--config`、`--backend` |
| `bash start.sh` | 全流程 + 体积统计 | — |
| `tools/weekly_run.py` | 跑一轮全流程并输出「日志 + RSS 变化 + 失败分类」的 JSON 汇总 | `--root`、`--keep-snapshots` |
| `tools/rss_diff.py` | 比较两份 RSS 快照，列出每个 feed 的新增/删除/变更 | `BEFORE_DIR AFTER_DIR` |

所有带 `--config` 的命令默认读 `./config.yaml`。

## 产物结构

本地 `output/` 的结构就是桶里的 key 结构（同步时原样镜像）：

```
output/
├── index.html                          # 订阅首页
├── rss/
│   ├── season/{sid}.xml                # 每个 all 合集一条
│   ├── series/{sid}.xml
│   └── new.xml                         # 跨合集的最新视频聚合流
├── bilibili-season/{sid}/
│   ├── meta.json                       # 合集元信息
│   ├── videos.json                     # 视频列表（含 pubdate，用于排序）
│   └── {bvid}/
│       ├── meta.json                   # 单视频详情（标题、简介、封面、cid…）
│       ├── pic.jpg                     # 封面
│       ├── {bvid}.m4a                  # 音频
│       └── complete                    # 幂等标记：存在即视为已完成
├── bilibili-series/{sid}/…             # 结构同上
└── bilibili-new/{sid}/…                # 只保留各合集最新 N 个
    └── {bvid}/
        ├── summary.json                # B站 AI 总结（归一化，含章节与时间点）
        └── chapters.json               # podcast:chapters 规范文件（可跳转 B站对应秒）
```

只有带 `complete` **且音频文件真实存在（且非空）**的视频才会进入 RSS；缺 `meta.json`、缺标记或缺音频的会被跳过并记 warning。

> 早期版本只信 `complete` 标记，导致「有标记但没音频」的视频照样生成 enclosure，客户端点开是 404。现在 `has_video_complete()` 同时校验音频，`b2p-fetch` 也会因此重新下载缺失音频的那一集。
> 下载过程中的中间产物（`.part` / `.ytdl` / `.m4s`）既不上传也不拉取，避免把截断的片段当成正式剧集发布。

### 同步规则（`b2p-sync`）

`b2p-sync` 只在 `local` 模式下有实际作用：把本地 `output/` 镜像到桶。

| 情况 | 行为 |
|---|---|
| 本地有、远端无 | 上传 |
| 远端大小 ≠ 本地大小 | 重传 |
| 大小相同但内容变了（≤1 MiB） | 用本地 md5 与远端 ETag 比对，不一致就重传 |
| `rss/**` 与根目录 `index.html` | **每轮强制重传**（内容会变，大小可能不变） |
| 远端有、本地无 | 删除 |
| 单个文件失败 | 记入 `failed`，`b2p-sync` 以非零码退出 |

上传时按扩展名设置 `ContentType`（`m4a → audio/mp4`、`xml → application/xml`、`html → text/html`），并设置 `Cache-Control`：音频/封面 `immutable`，其余 `max-age=300`。

在 `s3`/`r2` 模式下产物已经是直接写进桶的，`b2p-sync` 变成空操作（打印一行说明后返回 0），避免「用空的本地目录反向删掉远端」。

### 清理规则（`b2p-prune`）

合集和它的 feed 必须同生共死：合集目录被删之后，`rss/{kind}/{sid}.xml` 里的每一个 episode 都会指向 404。所以 `b2p-prune` 会连带处理 feed。

| 情况 | 行为 |
|---|---|
| 合集目录不在 `sources.json` 里 | 删目录，并删掉对应的 `rss/{kind}/{sid}.xml` |
| `rss/{kind}/` 下 sid 不在 `sources.json` 里（孤儿 feed） | 删掉这份 feed |
| `rss/new.xml` | 不按 sid 删——所有 new 合集共用这一份 |
| `--dry-run` | 只打印 `would delete (...)`，不真的删 |

> 孤儿 feed 是历史遗留问题：目录早被删了，feed 却留在桶里，订阅者看到的是一堆 404。
> 修复后每轮 `b2p-prune` 都会自动清掉它们。

### 响度归一化

新下载的音频会经过 ffmpeg `loudnorm` 归一化到 **−16 LUFS**（Apple Podcasts 的推荐值），
真峰值留足余量以避免 AAC 编码后削波。实测：一个 −34.7 LUFS 的源文件处理后为
**−16.3 LUFS / 真峰值 −0.4 dBFS**，体积反而略降（−5%）。

| 项 | 值 |
|---|---|
| 目标响度 | −16 LUFS |
| 响度范围 LRA | 11 LU |
| 真峰值目标（送入 loudnorm） | −3 dBTP（AAC 编码会再冲高约 2.6 dB） |
| 输出编码 | AAC-LC 64 kbps / 48 kHz / 立体声 |

关闭：`export B2P_NORMALIZE_LOUDNESS=0` 或 `audio.normalize_loudness: false`。

> 归一化只对**新下载**的音频生效，历史已发布的音频不会被重写。
> 代价：每集多花约 1 分钟分析 + 1 分钟编码（600 秒音频共约 3 分钟），
> 且需要机器上有 ffmpeg。


## 定时运行

```bash
# 内置调度（前台常驻，每 24h 一轮）
b2p-cron
```

或用系统 cron / systemd timer：

```cron
0 3 * * * cd /path/to/bilibili-to-podcast && /path/to/.venv/bin/bash start.sh >> log/cron.log 2>&1
```

### 每周巡检（带结果汇报）

`tools/weekly_run.py` 在 `start.sh` 外面包了一层：快照 `output/rss` → 跑流水线 → 再快照并 diff，把日志、RSS 变化和失败原因分类（cookie 过期 / 网络问题 / 代码缺陷）整理成 JSON 输出到 `reports/`。

```bash
.venv/bin/python tools/weekly_run.py
```

典型用法是配一条外部调度，让 agent 读它的 JSON 汇总：cookie 过期和网络抖动只汇报不修（外部环境问题），只有真正的代码异常才去改代码，改完必须跑 `pytest` 并重跑一次流水线确认。

## 开发

```bash
pip install -e ".[dev]"
pytest                     # 201 passed
pyright                    # 可选：类型检查（不在 dev 依赖里，需自行安装）
```

```
src/bilibili_podcast/
├── config.py              # 读 config.yaml + 环境变量
├── config_loader.py       # config.yaml + sources.json → 活动合集列表
├── extract_url.py         # 合集 URL 解析与校验
├── storage.py             # S3/R2 镜像与反向拉取（内容比对、Content-Type、并发）
├── store.py               # 存储抽象层：LocalStore / S3Store / Stage（远端工作目录）
├── rss.py                 # RSS XML 生成（路径全部由 output_root 推导）
├── xml_template.py        # RSS 模板
├── logger.py
├── bilibili/
│   ├── channel.py         # 合集遍历、视频下载调度
│   ├── audio.py           # yt-dlp + playurl 兜底、封面下载
│   ├── loudness.py        # ffmpeg loudnorm 响度测量与归一化
│   ├── credential.py      # Cookie 解析与 yt-dlp 安全副本文档
│   ├── meta.py            # 本地 JSON 读写、complete 标记与音频存在性校验
│   └── retry.py           # 412 / -352 退避重试
├── cli/                   # 每个 b2p-* 子命令一个模块
└── tools/                 # 每周巡检辅助脚本（非发布产物）
    ├── weekly_run.py      # 跑一轮流水线并输出 JSON 汇总
    └── rss_diff.py        # 比较 RSS 快照，列出新增/删除/变更
```

## 常见问题

**Q：`b2p-fetch` 大量 412？**
匿名访问视频详情接口会被风控。检查 Cookie 是否有效（`SESSDATA` 是否还在），或重新导出登录态 Cookie。日志里出现 `no SESSDATA in ...` 就是在提示这件事。

**Q：RSS 能打开，但 `index.html` 一点就下载？**
说明对象没有 `ContentType`。用当前版本跑 `b2p-sync` 会补上；旧版本上传的对象需要重传一次。

**Q：桶里内容没更新？**
早期版本只比对 `Size`，改一个字符但字节数不变的文件会漏传。现在 ≤1 MiB 的文件会额外比对 md5（S3 单段上传的 ETag 就是 md5），`rss/**` 与 `index.html` 仍然每轮强制重传。若仍怀疑未更新，删掉远端对象再同步一次即可。

**Q：某个视频下载失败会中断整轮吗？**
不会。单视频失败只记 error 并跳过（没有 `complete` 就不会进 RSS），重跑 `b2p-fetch` 会自动重试。

**Q：音频太大？**
默认 `worstaudio`（最低码率）。要换容器改 `src/bilibili_podcast/config.py` 的 `AUDIO_FORMAT`（支持 `m4a` / `mp3` / `opus` / `flac`，RSS 的 `enclosure type` 会自动跟随）。

**Q：声音比别的播客小？**
新下载的音频会自动归一化到 −16 LUFS（Apple Podcasts 标准），用 `export B2P_NORMALIZE_LOUDNESS=0` 可关闭。
**历史已发布的音频不会被自动重写**——如果那些旧的也偏轻，需要单独跑一次批量处理。

**Q：直连对象存储后，本地还需要留 output/ 吗？**
不需要。`storage.backend: r2` 时本地只留临时工作目录（下载/转码用，跑完即删）。
`b2p-pull` 仍可用于把桶里的东西拉回本地做离线分析。

**Q：description 里没有 AI 总结？**
B站只为部分视频生成了 AI 总结（本地实测约 85%），取不到时 description 回退到 UP 主简介。没取到结果的视频会在 7 天后自动重试（B站可能事后补生成）；想立刻重抓用 `b2p-summarize --force`。该接口需要登录 Cookie，匿名会返回 -101。

**Q：客户端里看不到章节？**
章节同时走两条路：description 里的 `HH:MM:SS 标题` 章节列表，以及 Podcasting 2.0 的 `podcast:chapters` 外链 JSON。
Apple Podcasts 的硬性要求（[官方说明](https://podcasters.apple.com/support/5482-using-chapters-on-apple-podcasts)）：**首章必须 00:00:00、至少 3 章、时间戳用 HH:MM:SS**；建议每小时不超过 6 章、标题不超过 45 字符。本项目已按此归零首章、截断标题、把章节数收到 12 以内。
注意：description 里不足 3 章时 Apple 会改用自动生成（需 iOS 26.2+）；`podcast:chapters` 需要客户端读取该标签，且章节文件必须先随 `b2p-sync` 上传到桶里。

**Q：想加/删合集？**
编辑 `sources.json` 后重跑 `bash start.sh`：新增的会抓全量，删除的会被 `b2p-prune` 清掉本地目录，桶里对应对象在下次 `b2p-sync` 时删除。

**Q：想清空重来？**
删 `output/` 与桶里的对象即可（桶会按本地状态镜像，删空后同步会把远端也删空，注意别误删其它数据）。

## License

[MIT](LICENSE)
