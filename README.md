<div align="center">

<img src="https://img.shields.io/github/license/TangMisaka23001/bilibili-to-podcast" alt="License">
<img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python">
<img src="https://img.shields.io/badge/yt--dlp-ffmpeg-orange" alt="yt-dlp">

</div>

# bilibili-to-podcast

将 B 站的视频**合集**和**系列**自动转成可在 Podcast 应用中订阅收听的 RSS Feed。

> 抓取视频 → 抽取音频 → 生成播客 XML → 同步到 R2 → 定时刷新

> 详细的模块说明、数据结构、扩展指引见 [`docs/FUNCTIONAL.md`](docs/FUNCTIONAL.md)。

## ✨ 特性

- 🎯 支持 B 站两种合集类型：**Season（合集）** 和 **Series（系列）**
- 🎵 用 `yt-dlp` + `ffmpeg` 抽取 `m4a` 音频（最低码率，文件最小）
- 🔄 自动翻页拉取合集内全部视频；`complete` 标记跳过已下载，**幂等可重跑**
- 📻 输出标准 RSS 2.0 + iTunes / Podcast Index namespace，兼容 Apple Podcasts、Pocket Casts 等主流客户端
- ☁️ 音频 + RSS 都同步到 Cloudflare R2（S3 兼容），挂任意域名即用
- ⏰ 内置 24h 定时调度，自动拉取新视频、清理残留目录
- 🐳 Docker 镜像自动构建发布到 Docker Hub

## 🚀 快速开始

### 安装

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

系统依赖：**ffmpeg**（macOS: `brew install ffmpeg`，Linux: `apt install ffmpeg`）

### 配置

```yaml
# config.yaml
RSS_URL_PREFIX: https://podcast.example.com/
PORT: 8000

sources:
  - https://space.bilibili.com/391930545/lists/598034?type=season
  - https://space.bilibili.com/14145636/lists/4891774?type=series

R2:
  ACCESS_KEY: '<your-r2-access-key>'
  SECRET_KEY: '<your-r2-secret-key>'
  ENDPOINT_URL: https://<account-id>.r2.cloudflarestorage.com
  BUCKET_NAME: bilibili-podcast
```

> `sources` 里直接写 B 站合集页 URL，系统自动解析 `uid` / `sid` / `type`。  
> 也兼容旧的 `season:` / `series:` 写法。

### 运行

```bash
# 一键全流程
sh start.sh

# 或分步执行
b2p-prune                     # 清理不在 config 中的旧目录
b2p-fetch                     # 拉取合集元数据 + 抽取音频
b2p-rss                       # 生成 RSS XML
b2p-sync                      # 同步到 R2

# 定时调度（每 24h 跑一轮）
b2p-cron

# 查看帮助
b2p-fetch --help
```

### Docker

```bash
docker build -t bilibili-podcast .
docker run -d \
  --name b2p \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/output:/app/output \
  bilibili-podcast
```

## 📂 项目结构

```
.
├── pyproject.toml
├── config.yaml.example
├── Dockerfile
├── docs/
│   └── FUNCTIONAL.md
├── tests/
│   └── test_*.py
├── start.sh                       # 一键脚本
└── src/
    └── bilibili_podcast/
        ├── config.py
        ├── config_loader.py
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
            ├── prune.py
            ├── fetch.py
            ├── rss_cmd.py
            ├── sync.py
            └── cron.py
```

## 🔄 数据流

```
config.yaml (sources / season / series)
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

## 🧪 测试

```bash
pip install -e ".[dev]"
pytest
# 35 passed
```

## 📋 TODO

- [ ] Vercel 部署
- [ ] UP 主全投稿视频
- [ ] AI 视频总结
- [ ] 分 P 视频支持
- [ ] 其他 OSS 扩展（阿里云 OSS、腾讯 COS）

## 📄 License

[MIT](LICENSE)
