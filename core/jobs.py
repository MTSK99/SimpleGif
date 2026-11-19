"""后台任务：一个转换任务 = 解析 -> 下载 -> 探测 -> 多轮转码 -> 交付。

为什么用后台线程而不是同步接口：
    单次转换 1~15 秒，体积压不下来时要转 5~6 轮，同步请求容易顶到浏览器超时。
    这里返回 job_id，前端轮询进度，顺带能把"分析调色板 / 渲染 GIF / 第 3 轮压缩"
    这类阶段文案显示出来，体验差别很大。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from typing import Any

from . import downloader, resolver, transcoder, ytdlp

DATA_DIR = os.path.abspath(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")))
JOBS_DIR = os.path.join(DATA_DIR, "jobs")

JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_HOURS", "6")) * 3600
MAX_JOBS = int(os.environ.get("MAX_JOBS", "200"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS), thread_name_prefix="convert")


def _ensure_dirs() -> None:
    os.makedirs(JOBS_DIR, exist_ok=True)


def _set(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def _size_text(size: int) -> str:
    return f"{size / 1048576:.2f}MB" if size >= 1048576 else f"{size / 1024:.0f}KB"


def _result_from_disk(job_id: str) -> dict[str, Any] | None:
    """服务重启后内存记录会丢，但成品还在磁盘上，这里用磁盘内容重建一份结果。"""
    workdir = os.path.join(JOBS_DIR, job_id)
    gif = os.path.join(workdir, "out.gif")
    if not os.path.isfile(gif):
        return None
    try:
        info = transcoder.inspect_gif(gif)
    except Exception:
        return None
    return {
        "url": f"/files/{job_id}/out.gif",
        "download_name": "wechat.gif",
        "size": info["size"],
        "size_text": _size_text(info["size"]),
        "width": info["width"],
        "height": info["height"],
        "frames": info["frames"],
        "duration": info["duration"],
        "params": None,
        "rounds": 1,
        "oversized": False,
        "limit_bytes": 0,
        "media": {
            "kind": "unknown",
            "title": "",
            "author": "",
            "thumbnail": "",
            "width": 0,
            "height": 0,
            "duration": 0.0,
            "via": "disk",
            "hls": False,
        },
        "source": None,
        "recovered": True,
    }


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            return dict(job)

    recovered = _result_from_disk(job_id)
    if not recovered:
        return None

    with _lock:
        entry = {
            "id": job_id,
            "state": "done",
            "stage": "完成",
            "progress": 1.0,
            "message": "",
            "result": recovered,
            "error": None,
            "created_at": os.path.getmtime(os.path.join(JOBS_DIR, job_id, "out.gif")),
            "updated_at": time.time(),
            "workdir": os.path.join(JOBS_DIR, job_id),
            "input": "",
            "options": {},
        }
        _jobs[job_id] = entry
        return dict(entry)


def public_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if not job:
        return None
    payload = {
        "id": job["id"],
        "state": job["state"],
        "stage": job["stage"],
        "progress": round(float(job["progress"]), 4),
        "message": job["message"],
        "result": job.get("result"),
        "error": job.get("error"),
        "elapsed": round(time.time() - job["created_at"], 2),
    }
    return payload


def submit(raw_input: str, opts: transcoder.GifOptions) -> str:
    _ensure_dirs()
    job_id = uuid.uuid4().hex[:12]
    workdir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(workdir, exist_ok=True)

    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "state": "queued",
            "stage": "排队中",
            "progress": 0.0,
            "message": "",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
            "workdir": workdir,
            "input": raw_input[:500],
            "options": asdict(opts),
        }

    _executor.submit(_worker, job_id, raw_input, opts, workdir)
    return job_id


def _worker(job_id: str, raw_input: str, opts: transcoder.GifOptions, workdir: str) -> None:
    src_path = os.path.join(workdir, "source.bin")
    gif_path = os.path.join(workdir, "out.gif")
    keep_source = os.environ.get("KEEP_SOURCE") == "1"

    try:
        _set(job_id, state="running", stage="解析链接", progress=0.02, message="")
        media = resolver.resolve(raw_input)
        _set(job_id, stage="解析链接", progress=0.06, message="已找到媒体地址")

        def on_dl(done: int, total: int) -> None:
            if total:
                p = 0.08 + 0.17 * min(1.0, done / total)
                msg = f"{done / 1048576:.1f}MB / {total / 1048576:.1f}MB"
            else:
                p = 0.12
                msg = f"已下载 {done / 1048576:.1f}MB"
            _set(job_id, progress=p, message=msg)

        if media.via == "ytdlp":
            # 非 X 站点：yt-dlp 负责握手、分片、签名，落成一个本地视频文件
            _set(job_id, stage="下载原文件", progress=0.08, message="由 yt-dlp 解析站点")
            downloaded = ytdlp.download(media.media_url, workdir, on_progress=on_dl)
            src = downloaded["path"]
            media.title = media.title or downloaded.get("title", "")
            media.author = media.author or downloaded.get("author", "")
            media.duration = media.duration or downloaded.get("duration", 0.0)
            media.width = media.width or downloaded.get("width", 0)
            media.height = media.height or downloaded.get("height", 0)
            _set(
                job_id,
                stage="下载原文件",
                progress=0.26,
                message=f"{downloaded['size'] / 1048576:.1f}MB",
            )
        elif media.hls:
            # HLS 直接交给 ffmpeg 按需拉流，不落地整段
            src = media.media_url
            _set(job_id, stage="读取视频信息", progress=0.2, message="HLS 流，直接转码")
        else:
            _set(job_id, stage="下载原文件", progress=0.08, message="")
            dl = downloader.download(media.media_url, src_path, on_progress=on_dl)
            src = dl["path"]
            _set(job_id, stage="下载原文件", progress=0.26, message=f"{dl['size'] / 1048576:.1f}MB")

        _set(job_id, stage="读取视频信息", progress=0.28)
        info = transcoder.probe(src)
        if info.duration <= 0:
            info.duration = 10.0  # 探测不到时长时给个合理上限，避免无限循环

        trimmed = False
        if opts.duration <= 0 and info.duration > 20:
            # GIF 体积几乎随时长线性膨胀：30 秒的动图塞进 5MB 只能压到 250px 上下，
            # 糊到没法用。长视频默认只取前 8 秒，需要更长的可以在高级设置里调。
            opts = replace(opts, duration=8.0)
            trimmed = True

        clip = opts.duration if opts.duration > 0 else info.duration
        clip_note = f"（原片 {info.duration:.0f}s，只取前 {clip:.0f}s）" if trimmed else ""
        _set(
            job_id,
            stage="转码",
            progress=0.3,
            message=f"{info.width}×{info.height} · {clip:.1f}s · {info.fps:.0f}fps{clip_note}",
        )

        def on_tc(progress: float, stage: str) -> None:
            _set(job_id, stage=stage, progress=0.3 + progress * 0.66)

        out = transcoder.transcode_with_budget(
            src,
            gif_path,
            opts,
            info,
            on_progress=on_tc,
            workdir=workdir,
        )
        _set(job_id, stage="收尾", progress=0.98)

        size = out["size"]
        entry = {
            "url": f"/files/{job_id}/out.gif",
            "download_name": _download_name(media),
            "size": size,
            "size_text": _size_text(size),
            "width": out["width"],
            "height": out["height"],
            "frames": out["frames"],
            "duration": out["duration"],
            "params": out.get("params"),
            "rounds": out.get("round", 1),
            "oversized": bool(out.get("oversized")),
            "trimmed": trimmed,
            "src_duration": round(info.duration, 1),
            "limit_bytes": opts.max_bytes,
            "media": media.to_public(),
            "source": out.get("source"),
        }

        # 桌面端可以直接点下载；手机上主要靠长按图片存相册
        _set(job_id, state="done", stage="完成", progress=1.0, message="", result=entry)

        if not keep_source:
            # src 可能是本地文件（直链下载 / yt-dlp 落地），也可能是 HLS 地址
            for candidate in {src_path, src}:
                if os.path.isfile(candidate):
                    try:
                        os.remove(candidate)
                    except OSError:
                        pass
    except (resolver.ResolveError, downloader.DownloadError, transcoder.TranscodeError) as exc:
        _set(job_id, state="error", stage="失败", message="", error=str(exc))
    except Exception as exc:  # 兜底，避免线程静默消失
        _set(job_id, state="error", stage="失败", message="", error=f"未预期的错误：{exc}")
    finally:
        cleanup()


def _download_name(media: resolver.Media) -> str:
    base = "".join(ch for ch in (media.author or "twitter") if ch.isalnum() or ch in "-_") or "twitter"
    return f"{base}-wechat.gif"


def cleanup() -> None:
    """清掉过期的任务目录，顺便把内存里的记录一起删。"""
    now = time.time()
    try:
        entries = []
        for name in os.listdir(JOBS_DIR):
            path = os.path.join(JOBS_DIR, name)
            if not os.path.isdir(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            entries.append((mtime, name, path))

        for mtime, name, path in entries:
            with _lock:
                job = _jobs.get(name)
                busy = bool(job and job.get("state") in ("queued", "running"))
            if busy:
                continue
            if now - mtime > JOB_TTL_SECONDS:
                shutil.rmtree(path, ignore_errors=True)
                with _lock:
                    _jobs.pop(name, None)

        entries = [e for e in entries if os.path.isdir(e[2])]
        if len(entries) > MAX_JOBS:
            entries.sort(key=lambda x: x[0])
            for _, name, path in entries[: len(entries) - MAX_JOBS]:
                with _lock:
                    job = _jobs.get(name)
                    busy = bool(job and job.get("state") in ("queued", "running"))
                if busy:
                    continue
                shutil.rmtree(path, ignore_errors=True)
                with _lock:
                    _jobs.pop(name, None)
    except OSError:
        pass


def job_dir(job_id: str) -> str | None:
    job = get_job(job_id)
    if not job:
        return None
    return job.get("workdir")
