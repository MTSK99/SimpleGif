"""把操作系统的代理设置自动接到环境变量上。

为什么需要这个文件：
    requests 和 ffmpeg 都只认 HTTP_PROXY / HTTPS_PROXY 环境变量，
    而「代理软件开着、浏览器能上网」用的是另一套东西——Windows 的系统代理
    存在注册表里，macOS 存在网络偏好里。两者互不相通。
    结果就是代理明明开着，脚本照样连不上 video.twimg.com。

这里在启动时读一次系统设置，补进环境变量，子进程（ffmpeg）也会继承。
用户显式设了环境变量则完全尊重用户的选择，不覆盖。
"""

from __future__ import annotations

import os
import subprocess
import sys

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

# 本机请求不该绕代理，否则健康检查和文件服务可能被绕进代理里
LOCAL_BYPASS = "localhost,127.0.0.1,::1"


def _normalize(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if "//" not in value:
        value = "http://" + value
    return value


def _parse_windows_server(raw: str) -> str | None:
    """ProxyServer 可能是 '127.0.0.1:7897'，也可能是 'http=1.2.3.4:8080;https=1.2.3.4:8081'。"""
    raw = raw.strip()
    if not raw:
        return None
    if "=" in raw:
        parts: dict[str, str] = {}
        for item in raw.split(";"):
            if "=" not in item:
                continue
            key, _, value = item.partition("=")
            parts[key.strip().lower()] = value.strip()
        raw = parts.get("https") or parts.get("http") or ""
    return _normalize(raw)


def _windows_proxy() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return None

    try:
        enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
        if not enabled:
            return None
        server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return None
    finally:
        key.Close()

    return _parse_windows_server(str(server))


def _macos_proxy() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["scutil", "--proxy"], capture_output=True, text=True, timeout=6
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    values: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()

    for scheme in ("HTTPS", "HTTP"):
        if values.get(f"{scheme}Enable") == "1":
            host = values.get(f"{scheme}Proxy") or ""
            port = values.get(f"{scheme}Port") or ""
            if host:
                return _normalize(f"{host}:{port}" if port else host)
    return None


def detect_system_proxy() -> str | None:
    return _windows_proxy() or _macos_proxy()


def ensure_proxy_env() -> tuple[str | None, bool]:
    """返回 (生效的代理地址, 是否由本程序自动注入)。

    已经设了环境变量就不动它——用户手动配的优先级最高。
    """
    existing = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY")
    if existing:
        for key in PROXY_KEYS:
            os.environ.setdefault(key, existing)
        os.environ.setdefault("no_proxy", LOCAL_BYPASS)
        os.environ.setdefault("NO_PROXY", LOCAL_BYPASS)
        return existing, False

    detected = detect_system_proxy()
    if not detected:
        return None, False

    for key in PROXY_KEYS:
        os.environ[key] = detected
    os.environ.setdefault("no_proxy", LOCAL_BYPASS)
    os.environ.setdefault("NO_PROXY", LOCAL_BYPASS)
    return detected, True
