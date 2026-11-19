"""mp4 -> gif 转码。

要点（决定成品在微信里能不能看、能不能发）：
    * 两遍调色板法：先 palettegen 统计全片用色，再 paletteuse 套用，
      比单遍 sRGB 量化干净得多，是 ffmpeg 官方推荐做法。
    * 帧率、宽度、时长、颜色数四个旋钮按梯度下降，直到塞进目标体积。
    * 微信"添加为表情"对单个 GIF 有体积上限，超了会导入失败，
      所以默认把 max_bytes 压到 5MB 以内并留出余量。

这里只负责"给定参数转一次"，多轮降级由 jobs.py 调度。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, replace

FFMPEG = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_PATH", "ffprobe")

# GIF 抖动算法。bayer 有序抖动体积小、压缩友好；sierra2_4a 观感更好但更大。
DITHERS = {"bayer", "sierra2_4a", "floyd_steinberg", "none"}


class TranscodeError(Exception):
    pass


@dataclass(frozen=True)
class GifOptions:
    width: int = 480          # 输出宽度，高度按比例
    fps: int = 12             # 输出帧率
    max_bytes: int = 5 * 1024 * 1024
    start: float = 0.0        # 起始秒
    duration: float = 0.0     # 截取时长，0 表示整段
    colors: int = 256         # 调色板颜色数，128 也能看但更小
    dither: str = "bayer"
    loop: int = 0             # 0 = 无限循环


@dataclass
class ProbeInfo:
    width: int
    height: int
    duration: float
    fps: float
    codec: str
    has_video: bool

    def to_public(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "duration": round(self.duration, 2),
            "fps": round(self.fps, 2),
            "codec": self.codec,
        }


def _run(cmd: list[str], timeout: float = 180) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def probe(src: str) -> ProbeInfo:
    code, out, err = _run(
        [
            FFPROBE,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            src,
        ],
        timeout=60,
    )
    if code != 0:
        raise TranscodeError(f"无法读取源文件信息（ffprobe 失败）：{err.strip()[:300]}")

    try:
        data = json.loads(out)
    except ValueError as exc:
        raise TranscodeError("ffprobe 输出无法解析。") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise TranscodeError("源文件里没有视频轨，无法转成 GIF。")

    duration = 0.0
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        try:
            duration = float(video.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0

    fps = 0.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = video.get(key) or ""
        if "/" in raw:
            num, _, den = raw.partition("/")
            try:
                n, d = float(num), float(den)
                if d:
                    fps = n / d
                    break
            except ValueError:
                continue
        else:
            try:
                fps = float(raw)
                break
            except ValueError:
                continue

    return ProbeInfo(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration=duration,
        fps=fps or 0.0,
        codec=str(video.get("codec_name") or ""),
        has_video=True,
    )


def _vf_chain(width: int, fps: int) -> str:
    # -2 保证高度为偶数不是必须的，但统一用 ; 连接避免滤镜串解析歧义
    return f"fps={fps},scale={width}:-1:flags=lanczos"


def _dither_expr(opts: GifOptions) -> str:
    dither = opts.dither if opts.dither in DITHERS else "bayer"
    if dither == "bayer":
        return "dither=bayer:bayer_scale=3:diff_mode=rectangle"
    if dither == "none":
        return "dither=none:diff_mode=rectangle"
    return f"dither={dither}:diff_mode=rectangle"


def _run_ffmpeg(cmd: list[str], total_seconds: float, on_progress, timeout: float) -> None:
    """执行 ffmpeg 并按 -progress 输出回报百分比进度。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stderr_tail: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms") and on_progress:
                try:
                    seconds = float(value) / 1_000_000.0
                except ValueError:
                    continue
                if total_seconds > 0:
                    on_progress(max(0.0, min(1.0, seconds / total_seconds)))
        assert proc.stderr is not None
        stderr_tail = proc.stderr.read().splitlines()[-8:]
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TranscodeError(f"转码超时（>{timeout:.0f} 秒），源文件可能过大或帧数过多。")
    finally:
        if proc.poll() is None:
            proc.kill()

    if proc.returncode != 0:
        detail = " | ".join(x.strip() for x in stderr_tail if x.strip())[:400]
        raise TranscodeError(f"ffmpeg 转码失败：{detail or '未知错误'}")


def transcode(
    src: str,
    out_gif: str,
    opts: GifOptions,
    info: ProbeInfo,
    on_progress=None,
    workdir: str | None = None,
) -> dict:
    """转一次 GIF。返回输出结果统计。"""
    workdir = workdir or os.path.dirname(out_gif) or "."
    palette = os.path.join(workdir, "palette.png")
    if os.path.exists(palette):
        os.remove(palette)
    if os.path.exists(out_gif):
        os.remove(out_gif)

    clip = opts.duration if opts.duration > 0 else max(0.0, info.duration - opts.start)
    if clip <= 0:
        clip = max(0.0, info.duration)

    base = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if opts.start > 0:
        base += ["-ss", f"{opts.start:.3f}"]
    if clip > 0:
        base += ["-t", f"{clip:.3f}"]
    base += ["-i", src]

    # 第一遍：统计调色板
    pass1 = base + [
        "-vf",
        f"{_vf_chain(opts.width, opts.fps)},palettegen=max_colors={opts.colors}:stats_mode=diff",
        "-frames:v", "1",
        "-progress", "pipe:1",
        "-nostats",
        palette,
    ]
    _run_ffmpeg(
        pass1,
        clip,
        (lambda p: on_progress(p * 0.32, "分析调色板")) if on_progress else None,
        timeout=180,
    )
    if not os.path.exists(palette):
        raise TranscodeError("调色板生成失败。")

    # 第二遍：套调色板输出 GIF
    pass2 = base + [
        "-i", palette,
        "-lavfi",
        f"{_vf_chain(opts.width, opts.fps)}[x];[x][1:v]paletteuse={_dither_expr(opts)}",
        "-loop", str(opts.loop),
        "-progress", "pipe:1",
        "-nostats",
        out_gif,
    ]
    _run_ffmpeg(
        pass2,
        clip,
        (lambda p: on_progress(0.32 + p * 0.68, "渲染 GIF")) if on_progress else None,
        timeout=240,
    )

    if not os.path.exists(out_gif) or os.path.getsize(out_gif) == 0:
        raise TranscodeError("没有生成 GIF 文件。")

    # ffmpeg 的 -progress 在短视频上可能只来得几帧就结束，
    # 这里补一次满进度，避免界面从 40% 直接蹦到 100%
    if on_progress:
        on_progress(1.0, "校验成品")

    try:
        if os.path.exists(palette):
            os.remove(palette)
    except OSError:
        pass

    return inspect_gif(out_gif, used=opts, src_info=info)


def inspect_gif(path: str, used: GifOptions | None = None, src_info: ProbeInfo | None = None) -> dict:
    """用 Pillow 读取成品信息，帧数准确（不靠估算）。"""
    size = os.path.getsize(path)
    width = height = 0
    frames = 0
    duration = 0.0
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            frames = int(getattr(img, "n_frames", 1) or 1)
            total_ms = 0
            for i in range(min(frames, 3000)):
                img.seek(i)
                total_ms += int(img.info.get("duration") or 0)
            duration = total_ms / 1000.0
    except Exception:
        pass

    result = {
        "path": path,
        "size": size,
        "width": width,
        "height": height,
        "frames": frames,
        "duration": round(duration, 2),
    }
    if used:
        result["params"] = {
            "width": used.width,
            "fps": used.fps,
            "colors": used.colors,
            "dither": used.dither,
            "start": used.start,
            "duration": used.duration,
            "max_bytes": used.max_bytes,
        }
    if src_info:
        result["source"] = src_info.to_public()
    return result


def _shrink(opts: GifOptions, round_index: int) -> GifOptions:
    """按梯度降级。顺序有意为之：先降帧率（对体积影响最大且观感损失最不明显），
    再降宽度、降颜色数，最后才截短时长。"""
    fps = max(6, int(round(opts.fps * 0.8)))
    width = max(160, int(opts.width * 0.85))
    colors = 128 if round_index >= 2 else opts.colors
    duration = opts.duration
    if round_index >= 3:
        if duration > 0:
            duration = max(2.0, duration * 0.8)
        else:
            duration = 6.0  # 没设时长就先截到 6 秒，长 GIF 很少有人看完
    return replace(opts, fps=fps, width=width, colors=colors, duration=duration)


def transcode_with_budget(
    src: str,
    out_gif: str,
    opts: GifOptions,
    info: ProbeInfo,
    on_progress=None,
    workdir: str | None = None,
    max_rounds: int = 6,
) -> dict:
    """反复转码直到体积进入预算，返回最后一轮结果。

    on_progress(fraction 0~1, stage 文案)
    若最终仍超标，结果里 oversized=True，由前端给出提示而不是静默返回大文件。
    """
    current = opts
    last: dict | None = None
    for index in range(max_rounds):
        note = "" if index == 0 else f"（第 {index + 1} 轮压缩）"

        def report(progress: float, stage: str, _note=note, _index=index):
            if on_progress:
                # 每轮占一段进度，后面的轮次留出余量
                span = 1.0 / max_rounds
                base = min(0.95, span * _index * 0.85)
                on_progress(min(0.99, base + progress * span), stage + _note)

        result = transcode(src, out_gif, current, info, on_progress=report, workdir=workdir)
        result["round"] = index + 1
        last = result
        if result["size"] <= current.max_bytes:
            last["oversized"] = False
            return last
        if index == max_rounds - 1:
            break
        current = _shrink(current, index)

    assert last is not None
    last["oversized"] = True
    return last
