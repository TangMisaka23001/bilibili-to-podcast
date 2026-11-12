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

## 特性

- 支持 B 站两种合集类型：`season`（合集）与 `series`（系列）
- 音频默认抽成 `m4a`（最低码率，单集通常几 MB～几十 MB）
- **幂等可重跑**：每个视频目录下有 `complete` 标记，已成功的不会重复下载
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

R2:
  ACCESS_KEY: xxxxxxxx
  SECRET_KEY: xxxxxxxx
  ENDPOINT_URL: https://<account-id>.r2.cloudflarestorage.com
  BUCKET_NAME: bilibili-podcast
```

> `config.yaml` 与 `cookie` 都在 `.gitignore` 里，不要提交。
> `b2p-sync` **只从 `config.yaml` 的 `R2` 段读凭据**，环境变量不会生效。
> 想放到别处：`export B2P_CONFIG_PATH=/path/to/config.yaml`（`sources.json` 需与它同目录）。

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
| `b2p-prune` | 删除已不在 `sources.json` 中的合集目录 | `--dry-run`、`--output-root` |
| `b2p-fetch` | 拉合集元数据 + 逐个视频下载音频 | `--new-top-n`（默认 5）、`--output-root` |
| `b2p-summarize` | 抓 B站 AI 总结 + 章节（只处理最新视频流） | `--force`、`--retry-days`、`--sleep` |
| `b2p-rss` | 生成 RSS XML | `--new-top-n`、`--output-root` |
| `b2p-gen-index` | 生成订阅首页 HTML | `--output`（默认 `<output-root>/index.html`） |
| `b2p-sync` | 把 `output/` 镜像到桶 | `--output-root` |
| `b2p-size` | 打印 `output/` 各子目录体积 | `--output-root` |
| `b2p-cron` | 每 24h 跑一轮 prune→fetch→summarize→rss→gen-index→sync | — |
| `bash start.sh` | 全流程 + 体积统计 | — |

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

只有带 `complete` 的视频才会进入 RSS；缺 `meta.json` 或缺标记的会被跳过并记 warning。

### 同步规则（`b2p-sync`）

| 情况 | 行为 |
|---|---|
| 本地有、远端无 | 上传 |
| 远端大小 ≠ 本地大小 | 重传（`list_objects_v2` 只有 Size，没有内容哈希） |
| `rss/**` 与根目录 `index.html` | **每轮强制重传**（内容会变，大小可能不变） |
| 远端有、本地无 | 删除 |
| 单个文件失败 | 记入 `failed`，`b2p-sync` 以非零码退出 |

上传时按扩展名设置 `ContentType`（`m4a → audio/mp4`、`xml → application/xml`、`html → text/html`），并设置 `Cache-Control`：音频/封面 `immutable`，其余 `max-age=300`。

## 定时运行

```bash
# 内置调度（前台常驻，每 24h 一轮）
b2p-cron
```

或用系统 cron / systemd timer：

```cron
0 3 * * * cd /path/to/bilibili-to-podcast && /path/to/.venv/bin/bash start.sh >> log/cron.log 2>&1
```

## 开发

```bash
pip install -e ".[dev]"
pytest                     # 143 passed
pyright                    # 可选：类型检查（不在 dev 依赖里，需自行安装）
```

```
src/bilibili_podcast/
├── config.py              # 读 config.yaml + 环境变量
├── config_loader.py       # config.yaml + sources.json → 活动合集列表
├── extract_url.py         # 合集 URL 解析与校验
├── storage.py             # S3/R2 镜像（内容比对、Content-Type、并发上传）
├── rss.py                 # RSS XML 生成（路径全部由 output_root 推导）
├── xml_template.py        # RSS 模板
├── logger.py
├── bilibili/
│   ├── channel.py         # 合集遍历、视频下载调度
│   ├── audio.py           # yt-dlp + playurl 兜底、封面下载
│   ├── credential.py      # Cookie 解析与 yt-dlp 安全副本文档
│   ├── meta.py            # 本地 JSON 读写
│   └── retry.py           # 412 / -352 退避重试
└── cli/                   # 每个 b2p-* 子命令一个模块
```

## 常见问题

**Q：`b2p-fetch` 大量 412？**
匿名访问视频详情接口会被风控。检查 Cookie 是否有效（`SESSDATA` 是否还在），或重新导出登录态 Cookie。日志里出现 `no SESSDATA in ...` 就是在提示这件事。

**Q：RSS 能打开，但 `index.html` 一点就下载？**
说明对象没有 `ContentType`。用当前版本跑 `b2p-sync` 会补上；旧版本上传的对象需要重传一次。

**Q：桶里内容没更新？**
`b2p-sync` 用 `Size` 比对，内容变了但大小巧合一致的文件不会自动重传——`rss/**` 和 `index.html` 已强制重传，其它自制文件请确保写入后触发一次修改大小，或临时删除远端对象。

**Q：某个视频下载失败会中断整轮吗？**
不会。单视频失败只记 error 并跳过（没有 `complete` 就不会进 RSS），重跑 `b2p-fetch` 会自动重试。

**Q：音频太大？**
默认 `worstaudio`（最低码率）。要换容器改 `src/bilibili_podcast/config.py` 的 `AUDIO_FORMAT`（支持 `m4a` / `mp3` / `opus` / `flac`，RSS 的 `enclosure type` 会自动跟随）。

**Q：description 里没有 AI 总结？**
B站只为部分视频生成了 AI 总结（本地实测约 85%），取不到时 description 回退到 UP 主简介。没取到结果的视频会在 7 天后自动重试（B站可能事后补生成）；想立刻重抓用 `b2p-summarize --force`。该接口需要登录 Cookie，匿名会返回 -101。

**Q：客户端里看不到章节？**
章节走 Podcasting 2.0 的 `podcast:chapters` 外链 JSON，Apple Podcasts / Pocket Casts / Overcast / Fountain / Podverse 支持，部分客户端不读该标签。章节文件需要先随 `b2p-sync` 上传到桶里才能被拉到。

**Q：想加/删合集？**
编辑 `sources.json` 后重跑 `bash start.sh`：新增的会抓全量，删除的会被 `b2p-prune` 清掉本地目录，桶里对应对象在下次 `b2p-sync` 时删除。

**Q：想清空重来？**
删 `output/` 与桶里的对象即可（桶会按本地状态镜像，删空后同步会把远端也删空，注意别误删其它数据）。

## License

[MIT](LICENSE)
