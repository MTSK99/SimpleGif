"""把媒体直链下载到本地；HLS 交给 ffmpeg 直接读，不走这里的下载器。"""

from __future__ import annotations

import os
from typing import Callable

import requests

from .resolver import UA, assert_media_host

CHUNK = 256 * 1024


class DownloadError(Exception):
    pass


def _limit_bytes() -> int:
    try:
        value = int(os.environ.get("MAX_DOWNLOAD_MB", "60"))
    except ValueError:
        value = 60
    return max(1, value) * 1024 * 1024


def download(
    url: str,
    dest_path: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """流式下载到 dest_path。

    on_progress(done_bytes, total_bytes)，total 未知时为 0。
    返回 {path, size, content_type}。
    """
    assert_media_host(url)
    limit = _limit_bytes()

    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": "https://x.com/",
    }
    try:
        with requests.get(url, headers=headers, stream=True, timeout=(10, 60), allow_redirects=True) as resp:
            if resp.status_code != 200:
                raise DownloadError(f"媒体地址返回 HTTP {resp.status_code}，可能已失效。")
            final_url = resp.url or url
            # 跟随重定向后仍要校验域名，避免被跳到任意地址
            try:
                assert_media_host(final_url)
            except Exception:
                raise DownloadError("媒体地址被重定向到了不受信任的域名，已中止。")

            total = 0
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                total = 0
            if total and total > limit:
                raise DownloadError(
                    f"源文件 {total / 1048576:.1f}MB 超过上限 {limit // 1048576}MB，"
                    "可调大 MAX_DOWNLOAD_MB 环境变量。"
                )

            done = 0
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    done += len(chunk)
                    if done > limit:
                        raise DownloadError(
                            f"源文件超过上限 {limit // 1048576}MB，已中止下载。"
                            "可调大 MAX_DOWNLOAD_MB 环境变量。"
                        )
                    fh.write(chunk)
                    if on_progress:
                        on_progress(done, total)
    except requests.RequestException as exc:
        raise DownloadError(f"下载失败：{exc}") from exc

    size = os.path.getsize(dest_path)
    if size < 1024:
        raise DownloadError("下载到的文件过小，可能是错误页面而不是视频。")
    return {"path": dest_path, "size": size}
