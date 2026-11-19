"""用 yt-dlp 处理非 X 站点的链接。

为什么不自研解析：
    各家站点的播放地址带签名、分片加密、CDN 拓扑各不相同，而且经常变。
    yt-dlp 有社区维护的上千个站点提取器，是这类需求唯一现实的选择。
    自己写等于给每个站点维护一份随时会坏的规则。

这里只让它做一件事：**把链接变成本地的一个视频文件**。
转码仍然交给 transcoder，进度也接进同一条进度条。
"""

from __future__ import annotations

import glob
import os
from urllib.parse import urlparse

# 转 GIF 不需要 4K，限制一下高度能省一半下载时间和流量
MAX_HEIGHT = 720

# 单次下载上限，和 downloader 保持一致
MAX_BYTES = 120 * 1024 * 1024


class YtdlpError(Exception):
    """解析或下载失败，message 直接展示给用户。"""


def _load():
    try:
        import yt_dlp  # noqa: F401
        return yt_dlp
    except ImportError as exc:  # pragma: no cover - 取决于部署环境
        raise YtdlpError(
            "服务器没有安装 yt-dlp，无法解析非推特链接。\n执行 pip install yt-dlp 后重启即可。"
        ) from exc


def is_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def version() -> str:
    try:
        import yt_dlp
        return yt_dlp.version.__version__
    except Exception:
        return ""


def _check_scheme(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise YtdlpError("只支持 http/https 的网页链接。")


def _proxy() -> str | None:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def download(url: str, workdir: str, on_progress=None) -> dict:
    """下载到 workdir/source.<ext>，返回 {path, title, author, duration, width, height}。

    on_progress(done_bytes, total_bytes)，total 未知时为 0。
    """
    _check_scheme(url)
    yt_dlp = _load()
    os.makedirs(workdir, exist_ok=True)

    def hook(status: dict) -> None:
        if not on_progress:
            return
        done = int(status.get("downloaded_bytes") or 0)
        total = int(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        if done > MAX_BYTES:
            raise YtdlpError("源文件超过 120MB 上限，已中止下载。")
        on_progress(done, total)

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        # 只要视频轨，不合并音频——转 GIF 用不到声音，省掉一次 ffmpeg 合并
        "format": f"bv*[height<={MAX_HEIGHT}]/b[height<={MAX_HEIGHT}]/b",
        "outtmpl": os.path.join(workdir, "source.%(ext)s"),
        "progress_hooks": [hook],
        "socket_timeout": 25,
        "retries": 2,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
        "nocheckcertificate": False,
    }
    proxy = _proxy()
    if proxy:
        options["proxy"] = proxy

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except YtdlpError:
        raise
    except Exception as exc:
        message = str(exc)
        # yt-dlp 的报错很长，取第一行给用户看
        first_line = message.strip().splitlines()[0] if message.strip() else "未知错误"
        if "Unsupported URL" in message:
            raise YtdlpError(
                f"这个站点还不支持：{first_line}\n"
                "yt-dlp 覆盖绝大多数视频站，但私有链接、需要登录的内容、纯网页链接都解析不了。"
            ) from exc
        raise YtdlpError(f"解析失败：{first_line[:200]}") from exc

    files = [p for p in glob.glob(os.path.join(workdir, "source.*")) if os.path.isfile(p)]
    if not files:
        raise YtdlpError("下载完成但找不到输出文件。")
    path = max(files, key=os.path.getsize)

    info = info or {}
    return {
        "path": path,
        "size": os.path.getsize(path),
        "title": (info.get("title") or "").strip(),
        "author": (info.get("uploader") or info.get("channel") or "").strip(),
        "duration": float(info.get("duration") or 0),
        "width": int(info.get("width") or 0),
        "height": int(info.get("height") or 0),
        "webpage": info.get("webpage_url") or url,
    }
