"""简单gif 服务入口。

    python app.py

浏览器访问 http://127.0.0.1:8000 ；手机在同一 Wi-Fi 下访问启动时打印的局域网地址。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import asdict

import requests
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from core import jobs, netenv, transcoder

try:
    # 控制台直连时交给 Python 自己处理（Windows 上走控制台 API，中文不会乱码）；
    # 被重定向到文件或管道时才统一成 UTF-8，避免非 UTF-8 locale 下打印中文直接崩。
    for _stream in (sys.stdout, sys.stderr):
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 必须早于任何网络请求：把系统代理接进环境变量，requests 与 ffmpeg 才认
ACTIVE_PROXY, PROXY_AUTO = netenv.ensure_proxy_env()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.json.ensure_ascii = False

JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")

# 预设：都是"能直接塞进微信"的方向，不是给设计师用的
PROFILES: dict[str, dict] = {
    # 微信「添加为表情」：尺寸小、体积必须压到 5MB 以下，超过会导入失败
    "emoji": {
        "label": "微信表情",
        "desc": "≤4.8MB · 表情库",
        "width": 320,
        "fps": 12,
        "max_mb": 4.8,
        "colors": 256,
        "dither": "bayer",
    },
    # 微信聊天里直接发：可以大一点，画面更清楚
    "chat": {
        "label": "聊天大图",
        "desc": "≤8MB · 聊天",
        "width": 480,
        "fps": 15,
        "max_mb": 8.0,
        "colors": 256,
        "dither": "bayer",
    },
    # 存到相册自己看 / 发给别的平台
    "hd": {
        "label": "高清",
        "desc": "≤15MB · 画质优先",
        "width": 640,
        "fps": 15,
        "max_mb": 15.0,
        "colors": 256,
        "dither": "sierra2_4a",
    },
}

DEFAULT_PROFILE = "emoji"


def clamp(value, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def build_options(payload: dict) -> transcoder.GifOptions:
    profile_key = str(payload.get("profile") or DEFAULT_PROFILE)
    base = PROFILES.get(profile_key, PROFILES[DEFAULT_PROFILE])

    width = int(clamp(payload.get("width"), 80, 1280, base["width"]))
    fps = int(clamp(payload.get("fps"), 4, 30, base["fps"]))
    max_mb = clamp(payload.get("max_mb"), 0.3, 25.0, base["max_mb"])
    start = clamp(payload.get("start"), 0.0, 600.0, 0.0)
    duration = clamp(payload.get("duration"), 0.0, 60.0, 0.0)
    colors = int(clamp(payload.get("colors"), 8, 256, base["colors"]))
    dither = str(payload.get("dither") or base["dither"])
    if dither not in transcoder.DITHERS:
        dither = "bayer"

    return transcoder.GifOptions(
        width=width,
        fps=fps,
        max_bytes=int(max_mb * 1024 * 1024),
        start=start,
        duration=duration,
        colors=colors,
        dither=dither,
    )


@app.get("/")
def index():
    return render_template("index.html", profiles=PROFILES, default_profile=DEFAULT_PROFILE)


@app.get("/api/profiles")
def api_profiles():
    return jsonify({"default": DEFAULT_PROFILE, "profiles": PROFILES})


_net_cache: dict[str, float | bool | None] = {"ts": 0.0, "ok": None}


def probe_network() -> bool | None:
    """检测服务器能不能连到 X 的媒体域名。

    连上就算通（403/404 都说明 TLS 握手成功），测的是网络可达性而不是接口语义。
    结果缓存 2 分钟，避免每次开页面都等一次超时。
    """
    now = time.time()
    cached = _net_cache.get("ok")
    if cached is not None and now - float(_net_cache["ts"] or 0) < 120:
        return bool(cached)
    try:
        requests.head("https://video.twimg.com/", timeout=(3, 3), allow_redirects=False)
        ok = True
    except Exception:
        ok = False
    _net_cache.update({"ts": now, "ok": ok})
    return ok


def has_proxy_env() -> bool:
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        if os.environ.get(key):
            return True
    return False


@app.get("/api/health")
def api_health():
    ffmpeg_ok = True
    try:
        proc = subprocess.run([transcoder.FFMPEG, "-version"], capture_output=True, timeout=8)
        ffmpeg_ok = proc.returncode == 0
    except Exception:
        ffmpeg_ok = False

    return jsonify(
        {
            "ok": ffmpeg_ok,
            "ffmpeg": ffmpeg_ok,
            "network": probe_network(),
            "proxy": has_proxy_env(),
        }
    )


@app.post("/api/jobs")
def api_create_job():
    payload = request.get_json(silent=True) or {}
    raw = str(payload.get("input") or "").strip()
    if not raw:
        return jsonify({"error": "请先粘贴推文链接或 GIF 直链。"}), 400
    if len(raw) > 4000:
        return jsonify({"error": "输入太长了，只粘贴一条链接即可。"}), 400

    opts = build_options(payload)
    job_id = jobs.submit(raw, opts)
    return jsonify({"id": job_id, "options": asdict(opts)}), 202


@app.get("/api/jobs/<job_id>")
def api_get_job(job_id: str):
    if not JOB_ID_RE.match(job_id):
        abort(404)
    data = jobs.public_job(job_id)
    if not data:
        return jsonify({"error": "任务不存在或已过期。"}), 404
    return jsonify(data)


@app.get("/files/<job_id>/<path:filename>")
def serve_file(job_id: str, filename: str):
    if not JOB_ID_RE.match(job_id):
        abort(404)
    if filename != "out.gif":
        abort(404)
    directory = jobs.job_dir(job_id)
    if not directory or not os.path.isdir(directory):
        abort(404)
    response = send_from_directory(directory, filename, mimetype="image/gif", max_age=3600)
    # 微信/浏览器都按原图缓存，这里不加 no-store，避免长按保存时二次请求失败
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "页面不存在。"}), 404


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("223.5.5.5", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    ip = lan_ip()
    print("=" * 56)
    print("  简单gif 已启动")
    print(f"  电脑访问：http://127.0.0.1:{port}")
    print(f"  手机访问：http://{ip}:{port}   （需同一 Wi-Fi）")
    if ACTIVE_PROXY:
        source = "自动读取系统设置" if PROXY_AUTO else "来自环境变量"
        print(f"  代理：{ACTIVE_PROXY}（{source}）")
    else:
        print("  代理：未检测到（若访问不了 X，请设置 HTTPS_PROXY）")

    # 启动就把可达性测掉，页面打开时健康检查能立刻拿到结果
    reachable = probe_network()
    print(
        "  网络：video.twimg.com 可达"
        if reachable
        else "  网络：连不上 video.twimg.com —— 转换会失败，先解决代理"
    )
    print("=" * 56)

    app.run(host=host, port=port, threaded=True, debug=False)
