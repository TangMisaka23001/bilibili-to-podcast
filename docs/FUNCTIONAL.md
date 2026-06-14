# 功能文档

本文档面向开发者，详细描述每个模块的实现细节、数据格式与扩展点。

## 1. 架构总览

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  B 站 API    │ →  │  本地 output  │ →  │  R2 对象存储  │
│ (bilibili-   │    │  (../output) │    │  (boto3)    │
│  api)        │    │              │    │              │
└──────────────┘    └──────────────┘    └──────────────┘
                                               │
                                               ↓
                                      ┌──────────────┐
                                      │  RSS XML     │
                                      │  生成 + 上传  │
                                      └──────────────┘
                                               │
                                               ↓
                                      ┌──────────────┐
                                      │  Podcast App │
                                      └──────────────┘
```

主入口 `src/start.sh`：

```bash
python bilibili_season.py && \
python bilibili_series.py && \
python upload_r2.py        && \
python bilibili_rss.py     && \
python upload_r2.py
```

每步都是阻塞的，前一步失败后续不会执行——这是有意为之，避免在数据不完整时生成错误 RSS。

---

## 2. 配置层（`config.py`）

启动时调用 `load_global_config()`，读取 `../config.yaml` 并填充全局变量：

| 全局变量 | 来源 | 说明 |
|---------|------|------|
| `config` | 整个 yaml | 给 season / series 模块直接读取合集列表 |
| `RSS_URL_PREFIX` | `RSS_URL_PREFIX` | 拼接 RSS XML 中 `enclosure url` 和 `atom:link` |
| `PORT` | `PORT` | 当前未使用，预留给未来的 HTTP 服务 |
| `ACCESS_KEY` / `SECRET_KEY` / `ENDPOINT_URL` / `BUCKET_NAME` | `R2.*` | 给 boto3 客户端用 |
| `bilibili_link_prefix` | 写死 | 单视频页 URL 前缀 |
| `season_base_path` / `series_base_path` | 写死 | 本地输出根目录 |
| `season_rss_path` / `series_rss_path` | 写死 | R2 上的 key 前缀（注意：**没有** `../output/`） |
| `AUDIO_FORMAT` | 写死 `m4a` | yt_dlp 后处理器输出的容器格式 |

---

## 3. Season 处理（`bilibili_season.py`）

针对 B 站的"合集"——一组被 UP 主手动归类的视频。

### 流程

1. **遍历 `config["season"]`**：
   - 创建 `output/bilibili-season/{sid}/` 目录
   - 调 `ChannelSeries(id, uid, type_=SEASON).get_meta()` 拿合集元信息
   - 写 `meta.json`

2. **拉全部视频列表**（翻页）：
   ```python
   while len(result) < channel_meta["media_count"]:
       page = await series.get_videos(sort=DEFAULT, pn=pn)
       result += page["archives"]
   ```
   写 `videos.json`。

3. **逐个 BV 处理**：
   - 检查 `output/bilibili-season/{sid}/{bv}/complete` 文件是否存在 → 存在则跳过
   - 创建 `output/bilibili-season/{sid}/{bv}/` 目录
   - 调 `video_api.Video(bvid=bv).get_info()` 拿视频元信息（删掉 `ugc_season` 字段避免循环引用）
   - 写 `{bv}/meta.json`
   - 下载音频（yt_dlp 抽 m4a）
   - 下载封面（requests 直接拉）
   - 写 `{bv}/complete` 标记完成

### 关键数据结构

**合集 meta.json**（`ChannelSeries.get_meta()` 返回）：
```json
{
  "id": 598034,            // 合集 ID（对应 config 里的 sid）
  "mid": 391930545,        // UP 主 UID
  "title": "...",
  "cover": "https://...",
  "upper": {"name": "...", "mid": ...},
  "media_count": 42        // 视频总数，用于翻页终止条件
}
```

**视频 meta.json**（`Video.get_info()` 返回）：
```json
{
  "bvid": "BV1...",
  "title": "...",
  "desc": "...",
  "pic": "https://...",    // 封面 URL
  "duration": 1234,        // 秒
  "pubdate": 1700000000    // Unix 时间戳
}
```

### 注意

- Season 的 complete 检查走**本地文件**（`has_channel_video_complete`），与 Series 不一致（Series 走 R2）。详见第 8 节"已知不一致"。
- `get_video_info` 的 `del video_info["ugc_season"]` 是为了打破 JSON 自引用，否则 json.dump 会无限递归。

---

## 4. Series 处理（`bilibili_series.py`）

针对 B 站的"系列"——一种比 Season 更轻量的视频归类（通常对应"默认合集"）。

### 与 Season 的差异

| 维度 | Season | Series |
|------|--------|--------|
| `ChannelSeriesType` | `SEASON` | `SERIES` |
| 合集 meta 字段 | `id` / `media_count` | `series_id` / `total` |
| 翻页顺序 | `ChannelOrder.DEFAULT` | `ChannelOrder.CHANGE`（按更新时间） |
| complete 检查 | 本地文件 | **R2 对象存储**（通过 `file.has_season_video_complete`，但实际是查 season 路径，**这里是 bug，见第 8 节**） |
| 单视频失败 | 抛错中断流程 | `try/except` 跳过 |

### 流程

完全同 Season，但额外加了逐视频的 `try/except`，保证一个 BV 失败不会拖垮整个 series。

---

## 5. 音频下载（`bilibili_audio_download.py`）

### yt_dlp 配置

```python
{
    "format": "worstaudio/worst",     # 最低码率音频流，文件最小
    "postprocessors": [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "m4a",      # 输出 m4a 容器
    }],
    "outtmpl": "{base_path}/{channel}/{bv}/{bv}",
    "cookiefile": "cookie"            # 会员/登录内容需要
}
```

- `worstaudio` 优先选音频流中码率最低的，回退到 `worst`（最差视频+音频）。
- ffmpeg 后处理器把音频流抽出来重新封装成 m4a。
- 输出文件名形如 `{bv}.m4a`，路径与 meta.json 同目录。

### 封面下载

直接 `requests.get(pic_url, stream=True).content` 写 `{bv}/pic.jpg`，没有缩放/压缩。如果想统一格式可在这一层加 PIL 处理。

### `cookie` 文件

B 站部分视频需要登录态（大会员、付费内容）。把浏览器导出的 cookies.txt 放到 `src/cookie`，yt_dlp 会自动使用。

---

## 6. RSS 生成（`bilibili_rss.py`）

### 输入

- `output/bilibili-{season|series}/{sid}/meta.json`（合集级）
- `output/bilibili-{season|series}/{sid}/videos.json`（视频列表）
- `output/bilibili-{season|series}/{sid}/{bv}/meta.json`（单视频）
- `output/bilibili-{season|series}/{sid}/{bv}/{bv}.m4a`（音频实际文件）

### 模板（`xml_template.py`）

三段字符串模板，用 `string.Template` 做 `$var` 替换：

- `feed_xml_template`：`<rss>` 顶层，声明 iTunes / Podcast Index / Atom / Content 四个 namespace
- `channel_template`：`<channel>` 中段，包含 title / description / itunes:author / itunes:image / items
- `item_template`：单集 `<item>`，包含 `<enclosure>` 指向音频 URL

### item 字段映射

| RSS 字段 | 数据来源 |
|---------|---------|
| `<title>` | `video_meta["title"]` |
| `<description>` | `video_meta["desc"]`（`&` 转 `&amp;`） |
| `<enclosure url>` | `RSS_URL_PREFIX + season_rss_path + channel + "/" + bv + "/" + bv + ".m4a"` |
| `<enclosure length>` | `os.path.getsize("../output/" + audio_path)`——**注意 series 才取真实大小，season 写死 0**（另一个不一致） |
| `<itunes:duration>` | `video_meta["duration"]`（秒） |
| `<itunes:image>` | `video_meta["pic"]` |
| `<pubDate>` | `formatdate(video_meta["pubdate"], usegmt=True)` |

### channel 字段

| RSS 字段 | 数据来源 |
|---------|---------|
| `<atom:link href>` | `RSS_URL_PREFIX + "rss/" + sid + ".xml"` |
| `<itunes:author>` | season 用 `meta["upper"]["name"]`；series 用 `meta["name"]` |
| `<title>` | season 用 `meta["title"]`；series 用 `meta["name"]` |
| `<link>` | 拼回 B 站合集页 URL |

### 输出

`output/rss/{season|series}/{sid}.xml`

---

## 7. 对象存储（`upload_r2.py` + `file.py`）

### boto3 客户端

```python
s3_client = boto3.session.Session().client(
    service_name="s3",
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    endpoint_url=ENDPOINT_URL,    # R2 的 endpoint
)
```

### 三个核心函数

| 函数 | 用途 |
|------|------|
| `object_exists(key)` | `head_object`，404 视为不存在 |
| `get_object(key)` | `get_object`，读取 + `json.loads`，返回 dict |
| `upload_files(local_folder)` | 递归遍历 `local_folder`，跳过 `videos.json` 的去重检查（强制重传），其余已存在跳过 |

### 上传流程

```python
upload_files("../output/rss",                check_exist=False)  # RSS 全量上传
upload_files("../output/bilibili-season",    check_exist=True)   # 增量上传音频/meta
upload_files("../output/bilibili-series",    check_exist=True)
```

### R2 上的目录结构

```
bilibili-podcast/
├── rss/
│   ├── season/{sid}.xml
│   └── series/{sid}.xml
├── bilibili-season/{sid}/
│   ├── meta.json
│   ├── videos.json
│   └── {bv}/
│       ├── meta.json
│       ├── pic.jpg
│       ├── {bv}.m4a
│       └── complete              # 空文件，作为幂等标记
└── bilibili-series/{sid}/...
```

`complete` 是空文件，存在即代表该 BV 已处理完成。

### `file.py` 的读路径

`bilibili_rss.py` 生成 XML 时，season 部分通过 `file.load_season_videos` / `load_season_video_meta` **从 R2 读**——保证 RSS 反映的是 R2 上的最新状态，而不是本地（防止本地删了但 R2 还有）。

> **注意**：`file.py` 只实现了 season 的读取，series 的 RSS 生成仍走本地 `load_series_videos` / `load_series_video_meta`（直接 `open()`）。

---

## 8. 已知不一致 / 改进点

来自代码 review 与 README TODO：

| # | 问题 | 位置 |
|---|------|------|
| 1 | Season 用本地 `complete`，Series 用 R2 上的 `complete`——两边行为不一致 | `bilibili_season.py:104` vs `file.py:4` |
| 2 | Series 的 RSS 生成读取本地 meta，但应该读 R2 才能反映真实在线状态 | `bilibili_rss.py:53-68` |
| 3 | Season 的 `<enclosure length>` 写死 `0`，Series 取真实大小 | `bilibili_rss.py:91 vs :115` |
| 4 | `bilibili_season.py` 没有逐视频 `try/except`，单个失败会中断整个 season 流程 | `bilibili_season.py:130-154` |
| 5 | `file.has_season_video_complete` 名字带 "season" 但被 `bilibili_season.py` 用作自身 complete 检查；语义混乱 | `file.py:4` |
| 6 | `config.py` 末尾有 `load_global_config()` 调用和模块级全局写入，import 副作用——测试和复用具不方便 | `config.py:45` |
| 7 | `docs/index.html` 写死了 `0xcafebabe.dpdns.org` 域名，无法直接复用 | `docs/index.html` |
| 8 | README TODO 中未实现：OSS 文件抽象统一、Vercel 部署、AI 视频总结、分 P 视频 | `README.md` |

---

## 9. 扩展指引

### 新增合集类型

B 站 API 文档：<https://nemo2011.github.io/bilibili-api/#/>

通常照搬 `bilibili_series.py`，改三个地方：

1. `ChannelSeriesType` 枚举值
2. meta / videos 字段名（`series_id` vs `id`，`total` vs `media_count`）
3. RSS 模板中的展示字段

### 改音频格式

`config.py` 顶部：

```python
AUDIO_FORMAT = 'mp3'  # 改这里
```

yt_dlp 的 `FFmpegExtractAudio` 后处理器支持 `mp3` / `m4a` / `opus` 等。

### 改 RSS 刷新频率

`src/cron.py`：

```python
schedule.every(24).hours.do(job)   # 改这里
```

支持 `minutes` / `hours` / `days` / 具体时间（`at('HH:MM')`）。

### 替换对象存储

`upload_r2.py` 用的是标准 boto3 S3 客户端，要换阿里云 OSS / 腾讯 COS：

- 改 endpoint URL
- 可能需要换成对应 SDK（OSS 的 `oss2` 库、COS 的 `cos-python-sdk-v5`）

### 暴露 HTTP 订阅页

`docs/index.html` 是现成的静态入口——直接通过对象存储的 public bucket 暴露即可。如果要自定义样式，可以改成 Vercel / Cloudflare Pages 部署。

---

## 10. 测试 / 验证

当前**没有测试**。建议覆盖：

- `bilibili_rss.py` 的 XML 生成：mock 掉 `file.load_season_*`，跑出 XML，对照 fixture
- `upload_r2.py` 的 `upload_files`：用 `moto` mock S3，验证调用次数和参数
- 翻页终止条件（`while len(result) < media_count`）：给一个假的 `ChannelSeries`，验证边界
- 幂等性：跑两遍 `start.sh`，验证第二次 `complete` 命中的 BV 不会被重新下载

## 11. 调试技巧

- 日志统一在 `../log` 文件 + stdout，按 `===>` 前缀过滤进度日志
- `config.py` 加载时会 `logger.info(config)` 把整个 yaml dump 出来——确认配置加载对
- 单步调试某个 BV：手动 `mkdir output/bilibili-season/{sid}/{bv}`，删掉 `complete`，重跑 `start.sh`，日志会精确到这一步
- yt_dlp 出问题：在 `download_channel_audio` 里加 `"verbose": True`
