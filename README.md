# bilibili-to-podcast

把哔哩哔哩的视频**合集**（Season / Series）自动转成可在 Podcast App 中订阅收听的 RSS Feed：抓取视频 → 抽取音频 → 生成播客 XML → 上传到对象存储 → 定时刷新。

> 详细的模块说明、数据结构、扩展指引见 [`docs/FUNCTIONAL.md`](docs/FUNCTIONAL.md)。

## 功能特性

- 支持两种 B 站合集类型：**Season（合集）** 和 **Series（系列）**
- 用 `yt_dlp` + `ffmpeg` 把视频抽成 `m4a` 音频（默认最低码率，文件最小）
- 自动翻页拉取合集内全部视频；已下载的视频通过 `complete` 标记跳过，**幂等可重跑**
- 生成标准 RSS 2.0 + iTunes / Podcast Index namespace 的 XML，兼容主流播客客户端
- 音频和 RSS 都上传到 Cloudflare R2（S3 兼容），可挂到任意 CDN / 自定义域名
- `cron.py` 每 24 小时自动刷新一次 RSS
- Docker 镜像发布到 Docker Hub（tag 触发）

## 快速开始

### 1. 找到合集的 uid 和 sid

打开任意 B 站合集页面，URL 形如：

```
https://space.bilibili.com/{uid}/channel/collectiondetail?sid={sid}
```

其中：

- `uid` 是 UP 主 ID
- `sid` 是合集 ID（注意：Season 用 `sid`，Series 用 `series_id`，两者不要混用）

### 2. 准备 `config.yaml`

复制示例文件并填入：

```bash
cp config.yaml.example config.yaml
```

```yaml
PORT: 8000
RSS_URL_PREFIX: https://podcast.example.com/   # 用户访问 RSS / 音频的前缀
FETCH_RECENT_N_VIDEOS: 0                        # 0 = 全量；>0 = 只取最近 N 个

season:
  - uid: 391930545
    sid: 598034

# series:
#   - uid: 3546729368520811
#   sid: 4281748

R2:
  ACCESS_KEY: '<your-r2-access-key>'
  SECRET_KEY: '<your-r2-secret-key>'
  ENDPOINT_URL: https://<account-id>.r2.cloudflarestorage.com
  BUCKET_NAME: bilibili-podcast
```

### 3. 本地运行

需要 Python 3.12+ 和 `ffmpeg`：

```bash
pip install -r requirements.txt
pip install git+https://github.com/Nemo2011/bilibili-api.git@dev
cd src && sh start.sh
```

`start.sh` 会依次执行：

```
bilibili_season.py  →  bilibili_series.py  →  upload_r2.py  →  bilibili_rss.py  →  upload_r2.py
```

启动定时调度：

```bash
cd src && python cron.py
```

### 4. Docker

```bash
docker build -t bilibili-podcast .
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/output:/app/output \
  bilibili-podcast
```

> 镜像由 `.github/workflows/tag-docker-build.yml` 在推送 tag 时自动构建并发布到 Docker Hub。

## 数据流

```
config.yaml (uid/sid)
   ↓
B 站 API (bilibili-api)
   ↓
../output/bilibili-{season|series}/{sid}/
    ├── meta.json              # 合集元信息
    ├── videos.json            # 视频列表
    └── {bvid}/
        ├── meta.json          # 单个视频元信息
        ├── pic.jpg            # 封面
        ├── {bvid}.m4a         # 音频
        └── complete           # 完成标记（幂等用）
   ↓
upload_r2.py → R2 bucket
   ↓
bilibili_rss.py
   ↓
../output/rss/{season|series}/{sid}.xml
   ↓
upload_r2.py → 通过 RSS_URL_PREFIX 暴露
   ↓
Podcast App 订阅
```

## 目录结构

```
.
├── config.yaml.example       # 配置示例
├── Dockerfile                # Python 3.12 + ffmpeg
├── docs/
│   ├── FUNCTIONAL.md         # 模块级功能文档
│   └── index.html            # 公开的订阅入口页面
├── requirements.txt
└── src/
    ├── bilibili_season.py    # Season 合集处理入口
    ├── bilibili_series.py    # Series 系列处理入口
    ├── bilibili_audio_download.py  # yt_dlp 抽音频 + 拉封面
    ├── bilibili_rss.py       # 扫描 output 生成 RSS XML
    ├── xml_template.py       # RSS / channel / item 模板
    ├── upload_r2.py          # boto3 上传 / 读取 R2
    ├── file.py               # 通过 R2 检查 complete / 读取 meta
    ├── config.py             # 加载 config.yaml
    ├── cron.py               # 24h 定时调度
    ├── logger.py             # 日志（写到 ../log + stdout）
    └── start.sh              # 主流程入口
```

## 常见问题

**Q：RSS 链接访问不到？**
A：检查 `RSS_URL_PREFIX` 是否和 R2 公开域名一致；R2 需要开启 Public Development URL 或绑自定义域。

**Q：某个 BV 下载失败？**
A：单视频失败会被 `bilibili_series.py` 的 `try/except` 跳过；Season 版未捕获，失败会中断整个流程——可以重跑，已完成的会跳过。

**Q：音频很大，磁盘撑不住？**
A：默认抽 `worstaudio`（最低码率）+ `m4a`，单集通常几 MB 到几十 MB。仍嫌大可改 `config.py` 的 `AUDIO_FORMAT` 为 `mp3` 或调 yt_dlp 格式。

## TODO

- [ ] 文件操作封装，支持本地和 OSS
- [ ] 支持 2 种合集类型（已完成 Season，Series 待统一抽象）
- [ ] 支持 Vercel 部署
- [ ] 支持 UP 主全投稿视频
- [ ] 支持使用流式接口替代文件访问形式
- [ ] docker build（CI 已就绪，本地待验证）
- [ ] github action（已就绪）
- [ ] support OSS upload（Cloudflare R2 已支持，其他 OSS 待扩展）
- [ ] AI 视频总结
- [ ] 分 P 视频转换为 podcast

## bilibili-api

文档：<https://nemo2011.github.io/bilibili-api/#/>

```
pip3 install git+https://github.com/Nemo2011/bilibili-api.git@dev
```

## License

MIT（详见 `LICENSE`）。
https://space.bilibili.com/{uid}/channel/collectiondetail?sid={sid}
```

其中：

- `uid` 是 UP 主 ID
- `sid` 是合集 ID（注意：Season 用 `sid`，Series 用 `series_id`，两者不要混用）

### 2. 准备 `config.yaml`

复制示例文件并填入：

```bash
cp config.yaml.example config.yaml
```

```yaml
PORT: 8000
RSS_URL_PREFIX: https://podcast.example.com/   # 用户访问 RSS / 音频的前缀
FETCH_RECENT_N_VIDEOS: 0                        # 0 = 全量；>0 = 只取最近 N 个

season:
  - uid: 391930545
    sid: 598034

# series:
#   - uid: 3546729368520811
#     sid: 4281748

R2:
  ACCESS_KEY: '<your-r2-access-key>'
  SECRET_KEY: '<your-r2-secret-key>'
  ENDPOINT_URL: https://<account-id>.r2.cloudflarestorage.com
  BUCKET_NAME: bilibili-podcast
```

### 3. 本地运行

需要 Python 3.12+ 和 `ffmpeg`：

```bash
pip install -r requirements.txt
pip install git+https://github.com/Nemo2011/bilibili-api.git@dev
cd src && sh start.sh
```

`start.sh` 会依次执行：

```
bilibili_season.py  →  bilibili_series.py  →  upload_r2.py  →  bilibili_rss.py  →  upload_r2.py
```

启动定时调度：

```bash
cd src && python cron.py
```

### 4. Docker

```bash
docker build -t bilibili-podcast .
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/output:/app/output \
  bilibili-podcast
```

> 镜像由 `.github/workflows/tag-docker-build.yml` 在推送 tag 时自动构建并发布到 Docker Hub。

## 数据流

```
config.yaml (uid/sid)
   ↓
B 站 API (bilibili-api)
   ↓
../output/bilibili-{season|series}/{sid}/
    ├── meta.json              # 合集元信息
    ├── videos.json            # 视频列表
    └── {bvid}/
        ├── meta.json          # 单个视频元信息
        ├── pic.jpg            # 封面
        ├── {bvid}.m4a         # 音频
        └── complete           # 完成标记（幂等用）
   ↓
upload_r2.py → R2 bucket
   ↓
bilibili_rss.py
   ↓
../output/rss/{season|series}/{sid}.xml
   ↓
upload_r2.py → 通过 RSS_URL_PREFIX 暴露
   ↓
Podcast App 订阅
```

## 目录结构

```
.
├── config.yaml.example       # 配置示例
├── Dockerfile                # Python 3.12 + ffmpeg
├── docs/
│   └── index.html            # 公开的订阅入口页面
├── requirements.txt
└── src/
    ├── bilibili_season.py    # Season 合集处理入口
    ├── bilibili_series.py    # Series 系列处理入口
    ├── bilibili_audio_download.py  # yt_dlp 抽音频 + 拉封面
    ├── bilibili_rss.py       # 扫描 output 生成 RSS XML
    ├── xml_template.py       # RSS / channel / item 模板
    ├── upload_r2.py          # boto3 上传 / 读取 R2
    ├── file.py               # 通过 R2 检查 complete / 读取 meta
    ├── config.py             # 加载 config.yaml
    ├── cron.py               # 24h 定时调度
    ├── logger.py             # 日志（写到 ../log + stdout）
    └── start.sh              # 主流程入口
```

## 常见问题

**Q：RSS 链接访问不到？**
A：检查 `RSS_URL_PREFIX` 是否和 R2 公开域名一致；R2 需要开启 Public Development URL 或绑自定义域。

**Q：某个 BV 下载失败？**
A：单视频失败会被 `bilibili_series.py` 的 `try/except` 跳过；Season 版未捕获，失败会中断整个流程——可以重跑，已完成的会跳过。

**Q：音频很大，磁盘撑不住？**
A：默认抽 `worstaudio`（最低码率）+ `m4a`，单集通常几 MB 到几十 MB。仍嫌大可改 `config.py` 的 `AUDIO_FORMAT` 为 `mp3` 或调 yt_dlp 格式。

## License

MIT（详见 `LICENSE`）。
