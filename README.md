<div align="center">

<img src="https://img.shields.io/github/license/TangMisaka23001/bilibili-to-podcast" alt="License">
<img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
<img src="https://img.shields.io/badge/yt--dlp-ffmpeg-orange" alt="yt-dlp">

</div>

# bilibili-to-podcast

将 B 站的视频**合集**和**系列**自动转成可在 Podcast 应用中订阅收听的 RSS Feed。

> 抓取视频 → 抽取音频 → 生成播客 XML → 同步到 R2 → 定时刷新

> 详细的模块说明、数据结构、扩展指引见 [`docs/FUNCTIONAL.md`](docs/FUNCTIONAL.md)。
> 设计文档见 [`docs/compose/specs/`](docs/compose/specs/)。
## ✨ 特性

- 🎯 支持 B 站两种合集类型：**Season（合集）** 和 **Series（系列）**
- 🎵 用 `yt-dlp` + `ffmpeg` 抽取 `m4a` 音频（最低码率，文件最小）
- 🔄 自动翻页拉取合集内全部视频；`complete` 标记跳过已下载，**幂等可重跑**
- 📻 输出标准 RSS 2.0 + iTunes / Podcast Index namespace，兼容 Apple Podcasts、Pocket Casts 等主流客户端
- ☁️ 音频 + RSS 都同步到 Cloudflare R2（S3 兼容），挂任意域名即用
- ⏰ 内置 24h 定时调度，自动拉取新视频、清理残留目录
- 🤖 GitHub Actions 每日自动同步，无需本地长驻进程
- 🐳 Docker 镜像自动构建发布到 Docker Hub

## 🚀 快速开始

### 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

系统依赖：**ffmpeg**（macOS: `brew install ffmpeg`，Linux: `apt install ffmpeg`）

### 配置

本项目不依赖 `config.yaml`。配置分两部分：

#### 1. 合集列表 — `sources.json`（项目根目录，必填）

```json
[
  "https://space.bilibili.com/391930545/lists/598034?type=season",
  "https://space.bilibili.com/14145636/lists/4891774?type=series"
]
```

填入要追踪的 B 站合集页 URL，系统自动从 URL 解析 `uid` / `sid` / `type`。  
文件缺失会启动失败（`ConfigError: sources file not found`）。

#### 2. 运行参数 — 环境变量

| 变量 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `RSS_URL_PREFIX` | ✅ | 播客 RSS 公开访问的前缀（必须等于 R2 公开域名） | `https://podcast.example.com/` |
| `PORT` | — | 本地 HTTP 端口（默认 `8000`） | `8000` |
| `R2_ACCESS_KEY` | ✅ | Cloudflare R2 Access Key ID | `xxxxxxxxxx` |
| `R2_SECRET_KEY` | ✅ | Cloudflare R2 Secret Access Key | `xxxxxxxxxx` |
| `R2_ENDPOINT_URL` | ✅ | R2 S3 Endpoint | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_BUCKET_NAME` | ✅ | R2 桶名 | `bilibili-podcast` |
| `B2P_COOKIE_CONTENT` | — | B 站 cookies.txt 全文（CI 场景使用，本地可直接放 `src/cookie` 文件） | 浏览器导出的内容 |

也可以放到 shell rc 或 `.env` 文件里 source 后再跑。  
本地开发最小可用配置：

```bash
export RSS_URL_PREFIX=http://localhost:8000
export R2_ACCESS_KEY=dev-access-key
export R2_SECRET_KEY=dev-secret-key
export R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
export R2_BUCKET_NAME=bilibili-podcast
```

#### 3. 可选：自定义 yaml 路径

默认 `config.yaml` 与 `sources.json` 同目录（项目根）。如果想把 `sources.json` 放到别处：

```bash
export B2P_CONFIG_PATH=/path/to/your/config.yaml
```

### 运行

```bash
# 一键全流程
bash start.sh

# 或分步执行
b2p-prune                     # 清理不在 sources 中的旧目录
b2p-fetch                     # 拉取合集元数据 + 抽取音频
b2p-rss                       # 生成 RSS XML
b2p-sync                      # 同步到 R2
b2p-gen-index                 # 生成 R2 桶首页 HTML（可选）

# 定时调度（每 24h 跑一轮）
b2p-cron

# 查看帮助
b2p-fetch --help
```

### Docker

```bash
docker build -t bilibili-to-podcast .
docker run -d \
  --name b2p \
  -v $(pwd)/sources.json:/app/sources.json:ro \
  -v $(pwd)/output:/app/output \
  -e RSS_URL_PREFIX=https://podcast.example.com/ \
  -e R2_ACCESS_KEY=xxx \
  -e R2_SECRET_KEY=xxx \
  -e R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com \
  -e R2_BUCKET_NAME=bilibili-podcast \
  bilibili-to-podcast
```

### GitHub Actions 自动同步

仓库自带 [`.github/workflows/sync.yml`](.github/workflows/sync.yml)，每天 UTC 03:00 自动跑一次 `start.sh`。

需要在仓库 **Settings → Secrets and variables → Actions** 添加以下 4 个 Secret：

- `R2_ACCESS_KEY`
- `R2_SECRET_KEY`
- `R2_ENDPOINT_URL`
- `R2_BUCKET_NAME`

`RSS_URL_PREFIX` 和 `PORT` 已在 workflow 文件中硬编码（按需修改）。  
也可在 GitHub UI 手动触发（Actions → Sync → Run workflow）。

## 📂 项目结构

```
.
├── pyproject.toml
├── sources.json                   # 合集 URL 列表（必填）
├── start.sh                       # 一键脚本
├── Dockerfile
├── .github/workflows/sync.yml     # 每日自动同步
├── docs/
│   ├── FUNCTIONAL.md
│   └── compose/specs/
├── tests/
│   └── test_*.py
└── src/
    └── bilibili_podcast/
        ├── config.py              # 全局配置（读 env）
        ├── config_loader.py       # sources.json → season/series
        ├── extract_url.py
        ├── storage.py
        ├── rss.py
        ├── xml_template.py
        ├── logger.py
        ├── bilibili/
        │   ├── channel.py
        │   ├── audio.py
        │   └── meta.py
        └── cli/
            ├── prune.py           # b2p-prune
            ├── fetch.py           # b2p-fetch
            ├── rss_cmd.py         # b2p-rss
            ├── sync.py            # b2p-sync
            ├── gen_index.py       # b2p-gen-index
            ├── cron.py            # b2p-cron
            └── _config_cli.py     # 旧 yaml→yaml 转换工具（独立 CLI）
```

## 🔄 数据流

```
sources.json (合集 URL 列表)
+ env: RSS_URL_PREFIX / R2_*
          ↓
     B 站 API (bilibili-api-python)
          ↓
     output/
     ├── bilibili-season/{sid}/{bvid}/
     │   ├── meta.json          # 合集元信息
     │   ├── videos.json        # 视频列表
     │   ├── {bvid}.m4a         # 音频
     │   ├── pic.jpg            # 封面
     │   └── complete           # 完成标记
     └── rss/{season|series}/{sid}.xml
          ↓
     R2 同步 (boto3) → 通过 RSS_URL_PREFIX 暴露
          ↓
     Podcast App 订阅
```

## ❓ FAQ

**Q：RSS 链接访问不到？**  
检查 `RSS_URL_PREFIX` 是否与 R2 公开域名一致，R2 需开启 Public URL 或绑自定义域。

**Q：某个视频下载失败？**  
单视频失败会 skip 并打 error 日志，不会中断整个合集处理。重跑 `b2p-fetch` 即可——已成功的 BV 会自动跳过。

**Q：音频太大？**  
默认 `worstaudio`（最低码率），单集通常几 MB 到几十 MB。可改 `AUDIO_FORMAT = 'mp3'`（`src/bilibili_podcast/config.py`）。

**Q：能加新的合集吗？**  
往 `sources.json` 数组里追加一行 URL，重跑 `bash start.sh` 即可。无需重启任何进程。

**Q：CI 跑失败了怎么看日志？**  
GitHub Actions → Sync 工作流 → 对应 run → 查看日志；`output/` 目录作为 artifact 上传，可下载到本地排查。

## 🧪 测试

```bash
pip install -e ".[dev]"
pytest
# 77 passed
```

## 📋 TODO

- [ ] Vercel 部署
- [ ] UP 主全投稿视频
- [ ] AI 视频总结
- [ ] 分 P 视频支持
- [ ] 其他 OSS 扩展（阿里云 OSS、腾讯 COS）

## 📄 License

[MIT](LICENSE)
