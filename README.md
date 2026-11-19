# SimpleGif

把 X（Twitter）上的动图转成微信能直接用的 GIF：贴一条链接，拿到可以在聊天里发、能加进表情库的动图。

Android 版在手机上本地转码，网页版在浏览器里跑，两边共用同一套解析与转码思路。

---

## 它解决什么问题

X 上的"GIF"其实不是 GIF 文件，而是 H.264 编码的 mp4，地址形如：

```
https://video.twimg.com/tweet_video/AbCdEf123.mp4
```

微信识别不了这种链接，直接转发只是个视频或干脆打不开。要发到聊天里、要加进表情库，必须是真正的 `.gif` 文件，而且要满足微信的体积限制。

这个工具做的就一件事：**粘贴链接 → 服务端 ffmpeg 转成合规 GIF → 手机保存 → 微信发送/加表情**。

## 功能

- 支持四种输入：推文链接（`x.com/用户名/status/...`）、整段分享文本、`video.twimg.com` 的 mp4 直链、**任意视频站点的页面地址**（B 站、YouTube、微博等，走 yt-dlp）
- 三档预设：微信表情（≤4.8MB、320px）、聊天大图（≤8MB、480px）、高清（≤15MB、640px）
- 体积超预算时自动降参重试：先降帧率，再降宽度、降颜色数，最后截短时长，最多 6 轮；压不下来会如实告诉你，不偷偷交付一个发不出去的巨型文件
- 高级参数：输出宽度、帧率、体积上限、起始秒、截取时长、调色板颜色数、抖动算法
- 纯黑白界面，跟随系统白天/深色模式自动切换，主色不占彩色
- 手机优先界面，可「添加到主屏」当独立 App 用；生成结果带 `#job=` 锚点，刷新页面或服务重启后仍能找回
- 服务器可达性自检：连不上 X 媒体域名时页面顶部直接告警，而不是让你等到下载失败才知道

## 支持哪些站点

| 来源 | 处理方式 |
| --- | --- |
| X / Twitter 推文 | 走 `api.fxtwitter.com`，快（1~2 秒），能拿到精确的时长和尺寸 |
| 任意外链（B 站、YouTube、微博、Reddit…） | 走 yt-dlp，覆盖上千个站点 |
| `video.twimg.com` 直链 | 直接下载 |

yt-dlp 是这件事唯一现实的方案：各家站点的播放地址带签名、分片加密、CDN 拓扑都不一样，而且经常改，自己写等于给每个站点维护一份随时会坏的规则。

它在服务器上跑，所以站点规则能随时用 `pip install -U yt-dlp` 更新——这一点是原生 App 做不到的（打包进 APK 的解析规则会随着时间失效）。

没装 yt-dlp 时，程序只会转推特链接，并在界面上告诉你缺了什么。

## 长视频会被截断

GIF 体积几乎随时长线性膨胀：一条 3 分半的视频塞进 5MB，只能压到 250px 上下，糊到没法看。

所以超过 20 秒的源默认**只取前 8 秒**，结果页会明确提示原片多长、实际转了多少。需要更长就在「高级设置」里把「截取秒数」调大，代价是画质下降。

## 两种形态

同一个需求，两种交付方式，按场景选一个：

| | 网页版（本目录） | Android 原生版（`android/`） |
| --- | --- | --- |
| 形态 | 手机浏览器打开，可加到主屏 | 独立 App |
| 转码位置 | 服务器 | 手机本地（ffmpeg 打进 APK） |
| 支持站点 | 推特 + 任意站点（yt-dlp，上千个） | 推特 + B 站（专用解析） |
| 需要服务器 | 是 | 否 |
| 体积 | 无 | APK 19.5MB |
| 适合 | 电脑上顺手用、给多个人用 | 自己手机上长期用 |

两个版本共用同一套思路：解析推文 → 拿到 `video.twimg.com` 的 mp4 → 两遍调色板转 GIF → 体积压到微信能吃下的范围。

Android 版的安装与构建说明见 [`android/README.md`](android/README.md)，编译好的安装包在 [Releases](../../releases) 里。

## 快速开始

### 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.10+ | 用到 `str \| None` 等新语法 |
| ffmpeg / ffprobe | 必须能被 `ffmpeg` 命令直接调到，或用 `FFMPEG_PATH` 指定 |
| yt-dlp | 可选。只转推特可以不装；要支持其他站点就 `pip install -U yt-dlp` |

安装 ffmpeg：

```powershell
# Windows
winget install Gyan.FFmpeg
```

```bash
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt install ffmpeg
```

### 安装运行

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python app.py
```

Windows 也可以直接双击 `start.bat`（它会优先用 `.venv\Scripts\python.exe`）。

启动后终端会打印两个地址：

```
  电脑访问：http://127.0.0.1:8000
  手机访问：http://192.168.1.16:8000   （需同一 Wi-Fi）
```

### 手机访问

1. 手机和电脑连同一个 Wi-Fi
2. 手机浏览器打开上面那个 `192.168.x.x` 地址
3. 浏览器菜单里选「添加到主屏幕」，之后就是一个图标形式的独立应用

**别在微信内置浏览器里打开这个页面**，微信的 WebView 对下载和长按保存限制较多，用系统浏览器（Safari / Chrome / Edge）打开更顺。

## 网络前提

这个工具本身不翻墙，它只是把链接转成 ffmpeg 能吃的格式。所以**运行它的机器必须能访问 `video.twimg.com`**。

**代理不用手动配。** 启动时会自动读取系统代理（Windows 读注册表里的 Internet 设置，macOS 读 `scutil --proxy`），补进 `HTTP_PROXY` / `HTTPS_PROXY`，ffmpeg 子进程也会继承。代理软件开着、浏览器能上网，这里就能用。

启动信息里会直接告诉你结果：

```
  代理：http://127.0.0.1:7897（自动读取系统设置）
  网络：video.twimg.com 可达
```

如果显示「未检测到」或「连不上」，只有两种情况：

- 代理软件没开系统代理模式（常见于只开了 TUN 之外的规则），或端口不在系统设置里 —— 这时手动指定一次即可：

  ```powershell
  # Windows
  $env:HTTPS_PROXY="http://127.0.0.1:7897"
  python app.py
  ```

  ```bash
  # macOS / Linux
  HTTPS_PROXY=http://127.0.0.1:7897 python app.py
  ```

  显式设置的环境变量优先级最高，程序不会覆盖你。

- 或者直接把服务部署到能访问 X 的机器上（境外 VPS 最省事）

页面加载时的健康检查会复用启动阶段那次探测结果，连不上会立刻在顶部红条里说明，不用等转换失败才发现。

## 使用说明

1. 打开推文，复制链接（App 里的「复制链接」、浏览器地址栏都行），或者直接分享整段文本粘贴进来
2. 选预设，需要的话展开「高级设置」微调
3. 点「生成微信 GIF」，看进度跑到完成
4. 保存到手机：
   - **iPhone**：长按预览图 → 「存储到照片」
   - **Android**：点「保存到手机」，或长按图片选下载
5. 发到微信：
   - 聊天发送：微信聊天 → `+` → 相册 → 选这张 GIF
   - 变成表情：在聊天里长按已发出的 GIF → 添加；或「我 → 表情 → 添加的单个表情 → +」从相册选

## 微信那边的限制

这些是长期沿用的经验值，微信从未公开文档化，版本不同可能有出入，**未逐一在所有版本上验证**：

| 场景 | 经验上限 | 本工具对应预设 |
| --- | --- | --- |
| 添加为自定义表情（单个动图） | 约 5MB，超了导入失败 | 微信表情（4.8MB） |
| 聊天里直接发送 | 宽松一些，但过大会被压缩或提示过大 | 聊天大图（8MB） |
| 存相册自己看 | 基本只受手机空间限制 | 高清（15MB） |

体积压不下来时，别硬撑：**把宽度调到 240、时长截到 3~4 秒**，几乎都能塞进 5MB。

## 技术实现

**解析层**（`core/resolver.py` + `core/ytdlp.py`）

分两条路：

- **X 链接**：输入里直接带的 `video.twimg.com` 直链 → 第三方公开解析接口（默认 `api.fxtwitter.com`）→ Twitter syndication 接口 → 推文页面 HTML 正则。拿到的 JSON 不做字段硬绑定，而是深度遍历搜集所有 `.mp4` 地址再排序：`tweet_video`（推文里的动图）优先，同档取路径里分辨率最高的变体。
- **其他站点**：交给 yt-dlp，由它完成握手、签名、分片下载，落成一个本地视频文件。

一个容易写错的细节：X 的媒体地址带查询串（`.../mEipTPWYPIwBGaZ_.mp4?tag=14`），判断扩展名必须只看 URL 的 path，直接 `endswith(".mp4")` 会把所有正常地址判成非视频，然后静默退回到更差的解析路径。

**下载层**（`core/downloader.py`）
流式下载 + 体积上限 + 域名白名单（默认只允许 `*.twimg.com`，精确匹配 host，跟随重定向后二次校验）。这一层是防 SSRF 的关键，别随手放开。

**转码层**（`core/transcoder.py`）
两遍调色板法，这是 ffmpeg 官方推荐的 GIF 生成姿势：

```bash
# 第一遍：统计全片用色，生成调色板
ffmpeg -i in.mp4 -vf "fps=12,scale=320:-1:flags=lanczos,palettegen=max_colors=256:stats_mode=diff" palette.png

# 第二遍：套用调色板输出
ffmpeg -i in.mp4 -i palette.png -lavfi \
  "fps=12,scale=320:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  -loop 0 out.gif
```

比单遍 `-pix_fmt rgb24` 干净得多。默认 `bayer` 抖动是因为它体积小、压缩友好；要观感优先可以切 `sierra2_4a`，代价是同样画面大 20~40%。

**体积预算**（`transcode_with_budget`）
转完用 `os.path.getsize` 实测，超了就按梯度重转，不是靠估算。降级顺序是刻意的：帧率对体积影响最大且观感损失最小，放第一位；时长最后动，因为截短会丢内容。

**任务层**（`core/jobs.py`）
线程池 + 内存状态表 + 前端轮询。单次转换 1~15 秒，压不下来时要转 6 轮，同步请求容易顶到浏览器超时，所以走后台任务。成品落盘后即使服务重启，`out.gif` 还在就能用磁盘内容重建结果，刷新页面不丢。

## 目录结构

```
简单gif/
├── app.py                  Flask 入口：路由、参数校验、预设、健康检查
├── core/
│   ├── resolver.py         链接 → 媒体直链（X 走接口，其他站点交给 yt-dlp）
│   ├── ytdlp.py            非 X 站点的通用解析与下载
│   ├── downloader.py       直链 → 本地文件（白名单 + 限流）
│   ├── transcoder.py       mp4 → gif（两遍调色板 + 体积预算降级）
│   ├── netenv.py           自动接管系统代理
│   └── jobs.py             后台任务、进度、落盘恢复、过期清理
├── templates/index.html    单页界面
├── static/                 样式、交互、图标、PWA manifest
├── android/                Android 原生版（Kotlin + ffmpeg-kit）
│   ├── app/src/main/java/com/simplegif/app/
│   │   ├── MainActivity.kt       界面与流程编排
│   │   ├── Resolver.kt           按站点分发
│   │   ├── TwitterResolver.kt    X 推文解析
│   │   ├── BiliResolver.kt       B 站解析
│   │   ├── VideoDownloader.kt    通用下载（按来源附带请求头）
│   │   ├── GifConverter.kt       本地 ffmpeg 转码
│   │   ├── GifStore.kt           保存相册与分享
│   │   └── Media.kt              媒体模型与域名守卫
│   └── README.md           安装与构建说明
├── assets/                 图标设计源（SVG）
├── tools/
│   ├── selftest.py         不联网自检（ffmpeg 链路 + 路由 + 解析规则）
│   └── make_icons.py       从 SVG 重新生成各尺寸图标
└── data/jobs/<id>/         运行时产物，默认 6 小时后自动清理（不入库）
```

## 配置项

全部通过环境变量，也可以写进 `.env.example` 复制出来的本地配置再手动导出。

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | 监听端口与地址 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 空 | 下载与 ffmpeg 走代理 |
| `RESOLVER_API` | fxtwitter、vxtwitter | 替换解析接口，逗号分隔 |
| `RESOLVE_TIMEOUT` | `12` | 解析接口超时（秒） |
| `MAX_DOWNLOAD_MB` | `60` | 源文件下载上限 |
| `ALLOW_EXTRA_HOSTS` | 空 | 额外允许的媒体域名，逗号分隔 |
| `FFMPEG_PATH` / `FFPROBE_PATH` | `ffmpeg` / `ffprobe` | 可执行文件路径 |
| `DATA_DIR` | `./data` | 运行时数据目录 |
| `JOB_TTL_HOURS` | `6` | 成品保留时长 |
| `MAX_JOBS` | `200` | 最多保留的任务目录数 |
| `MAX_WORKERS` | `2` | 同时转码数 |
| `KEEP_SOURCE` | `0` | 设为 `1` 保留下载的源文件，便于排查 |
| `DEBUG_RESOLVE` | `0` | 设为 `1` 时错误信息带每层解析失败原因 |

## 自检

不依赖外网的完整链路检查：

```bash
python tools/selftest.py
```

它会用 ffmpeg 现造一段测试视频，验证探测、转码、体积预算降级、截取时长，再跑一遍 Flask 路由、路径穿越防护和解析规则。全部通过时退出码为 0。

## 部署到服务器

单机自用直接 `python app.py` 就够。要给别人用，至少做两件事：

```bash
pip install waitress
waitress-serve --listen=0.0.0.0:8000 app:app
```

1. 换成生产 WSGI（上面这样），别用 Flask 自带的开发服务器
2. 配 HTTPS 域名——不是可选项：手机端「粘贴」按钮依赖剪贴板 API，而它在非安全上下文下被浏览器禁用；「添加到主屏」的完整能力也要求 HTTPS

## 边界

- 只处理你有权使用的公开内容，别拿它搬运别人的版权作品
- 推特解析依赖第三方公开接口，对方改协议或加风控时可能失效；失效时仍可用 `video.twimg.com` 直链兜底（浏览器开发者工具的 Network 面板筛 `.mp4` 就能拿到）
- 站外链接靠 yt-dlp，需要登录才能看的内容（会员视频、私密动态）同样解析不了，这是版权边界而非技术问题
- 只处理有视频轨的内容，纯图片、纯音频的页面会报错

## 许可证

[GPL-3.0](LICENSE)。

Android 版把 `ffmpeg-kit-full-gpl` 的 native 库打进了 APK，这个变体包含 GPL 组件（x264 等），所以分发 APK 必须遵守 GPL：要一并提供对应源代码，并允许再分发。

Web 版只是调用系统里安装的 ffmpeg 命令行，不链接它的库，本来不受 GPL 约束；为了不把同一个项目拆成两套许可，整个仓库统一用 GPL-3.0。
