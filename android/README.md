# SimpleGif · Android 版

贴一条 X（Twitter）推文或 B 站视频的链接，手机上直接转成微信能用的 GIF。

**不依赖服务器**：解析、下载、转码全在手机本地完成，ffmpeg 已经打进 APK。断网以外的情况都能用，也不需要电脑一直开着。

---

## 安装

APK 在 `app/build/outputs/apk/release/app-release.apk`，约 19.5MB。

传到手机（数据线、微信文件传输助手、网盘都行）后点开安装。首次安装需要在系统里允许「安装未知来源应用」。

**只支持 arm64 架构**，2020 年之后的手机基本都是。极老的 32 位设备装不上。

## 怎么用

1. 复制链接：
   - **X**：找到那条动图，点分享 → 复制链接
   - **B 站**：视频页分享 → 复制链接（`b23.tv` 短链也认）
2. 打开 App，点「粘贴」
3. 选一个预设：
   - **微信表情 · 4.8MB** —— 320px，可以加进表情库
   - **聊天大图 · 8MB** —— 480px，聊天里直接发
4. 点「生成 GIF」，等进度跑完
5. 点「保存到相册」，然后在微信里 `+` → 相册选图发送

想变成表情：在聊天里长按刚发出的动图 → 添加。

App 也支持从别的应用直接分享文本进来（会出现在系统分享面板里），粘贴时只取链接，不会把整段文案塞进去。

## 网络要求

解析和下载都要访问 X，**手机必须能打开 X 本身**。

开了 Clash 这类代理 App 的话，VPN 模式会自动接管所有应用的流量，这个 App 不需要任何额外设置——这点比在 Windows 上跑 Python 省心，那边还得手动配环境变量。

如果 X 打不开，App 会直接告诉你「连不上解析服务」，不会让你白等转码。

## 技术实现

**和网页版共用同一套思路，只是把执行位置从服务器挪到手机。**

| 环节 | 实现 |
|---|---|
| 解析分发 | `Resolver` 按域名分派：B 站走 `BiliResolver`，其余按推文处理 |
| X 解析 | `TwitterResolver` 调 `api.fxtwitter.com`，深度遍历 JSON 收集 `.mp4` 再按清晰度排序 |
| B 站解析 | `BiliResolver` 走 `x/web-interface/view` + `x/player/playurl` 公开接口 |
| 下载 | `VideoDownloader`，OkHttp 流式下载，按来源附带 Referer |
| 转码 | ffmpeg-kit 两遍调色板法（`palettegen` → `paletteuse`） |
| 转码 | ffmpeg-kit 两遍调色板法（`palettegen` → `paletteuse`） |
| 体积控制 | 超预算就降帧率、降宽度重转，最多 4 轮 |
| 保存 | MediaStore 写入 `Pictures/简单gif`，Android 10+ 不需要存储权限 |
| 预览 | `ImageDecoder` + `AnimatedImageDrawable`，原生播放 GIF |
| 配色 | 纯黑白，`Theme.Material3.DayNight` + `values-night` 跟随系统深浅色 |

界面是纯黑白的：浅色模式白底黑字、深色模式黑底白字，主色本身不占任何彩色。只有错误和警告保留红/黄——这两类信息靠颜色区分比靠风格统一更重要。

几处值得记下的细节：

- **媒体地址带查询串**（`.../xxx.mp4?tag=14`），判断扩展名必须只看 path，`endsWith(".mp4")` 会把正常地址判成非视频
- **同一条推文有多个清晰度变体**，从路径里的 `/720x720/` 读出分辨率，同档选最高的
- **`abiFilters` 只留 arm64-v8a**，APK 少一半；配合 R8 混淆，19.5MB（full-gpl 的 ffmpeg 本身就占大头）
- **`useLegacyPackaging = true`**，ffmpeg-kit 的 `.so` 必须传统方式打包才能被加载

B 站那两个坑是实测出来的，不是猜的：

- **请求头不全时 `view` 接口返回 412**（一串 HTML 错误页）。补上 `Accept-Language` 和 `Origin` 就正常，跟 cookie 无关——网上流传的"先访问主页拿 buvid3"其实是白做一次请求。
- **CDN 对移动端 UA 返回 403**，下载必须用桌面 UA + `Referer: https://www.bilibili.com/`。

## ffmpeg 的现状

官方 `com.arthenica:ffmpeg-kit` 已经在 2025 年 1 月退役，2025 年 4 月连预编译包都下架了。这里用的是社区维护版：

```kotlin
implementation("dev.ffmpegkit-maintained:ffmpeg-kit-full-gpl:8.1.9")
```

包名、API 全部与原版一致（`com.arthenica.ffmpegkit.*`），只换了 groupId，所以网上所有 ffmpeg-kit 的用法文档都适用。

选 `full-gpl` 而不是 `min`，是为了覆盖推特上各种编码（H.264 / HEVC / AV1）。已验证打进 APK 的 `.so` 里包含：

- `libavfilter.so`：`palettegen`、`paletteuse`、`scale`、`fps`
- `libavcodec.so`：`gif`、`h264`、`hevc`
- `libavformat.so`：`mp4`、`mov`

其中 `palettegen`/`paletteuse` 是能不能出好画质的关键，缺了就只能做粗糙的单遍量化。

**注意 GPL**：`full-gpl` 变体带 GPL 组件，分发这个 APK 需要遵守 GPL。自己用没有影响。

## 自己重新构建

需要 JDK 17+、Android SDK（platform 35 + build-tools 35）。

```powershell
$env:JAVA_HOME = "你的 JDK 路径"
cd android
gradle assembleRelease
```

没装 Gradle 的话，先下一个 8.9 版本，或者用 Android Studio 直接打开这个目录。

`local.properties` 里的 `sdk.dir` 指向本机 SDK，换机器要改。

依赖走阿里云镜像（`settings.gradle.kts`），因为 `maven.google.com` 在部分网络下直连不通。

**项目路径不能有中文**，否则 AGP 会拒绝构建。当前通过 `android.overridePathCheck=true` 放行，但如果碰到 aapt2 报路径错误，把这个目录复制到纯英文路径下再编译。

## 已知边界

- **没有在任何真机上验证过**。代码通过编译、APK 内容也逐项检查过，但实际跑起来的手感（转码耗时、内存占用、相册写入）需要你在手机上试
- 一次只处理一条推文，没有批量
- 转码上限 30 秒，超出的部分会被截掉
- 转码期间切后台可能被系统杀掉，建议留在前台
- 只处理公开内容：删除的推文、仅粉丝可见、B 站番剧/付费课程、纯图片页面都转不了
- 多 P 视频只取第一段
- 站外链接只支持 B 站。这是刻意的取舍：集成 yt-dlp 要打包 Python 运行时，APK 会从 19.5MB 涨到 55MB+，而且解析规则固化在安装包里，站点改版就得发新版本。需要更多站点时用网页版更划算
