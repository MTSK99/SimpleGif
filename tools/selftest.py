"""不联网自检：验证 ffmpeg 链路、体积预算降级、Flask 路由和链接解析规则。

    python tools/selftest.py

用 ffmpeg 现造一段测试视频当素材，不依赖任何外部网络。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import resolver, transcoder  # noqa: E402

OK = "[ OK ]"
FAIL = "[FAIL]"
results: list[tuple[bool, str]] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    results.append((cond, label))
    print(f"{OK if cond else FAIL} {label}" + (f" — {detail}" if detail else ""))


def make_sample(path: str, seconds: int = 6) -> bool:
    cmd = [
        transcoder.FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=640x360:rate=30:duration={seconds}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        # 部分构建没有 libx264，退回内置 mpeg4
        cmd[cmd.index("libx264")] = "mpeg4"
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0 and os.path.exists(path)


def test_resolver_rules() -> None:
    media = resolver.resolve("https://video.twimg.com/tweet_video/AbCdEf123.mp4")
    check(media.media_url.endswith(".mp4") and media.kind == "gif", "直链识别为推文动图")

    # 推文链接解析要联网，自检里只验证能正确提取推文 ID
    match = resolver.TWEET_ID_RE.search("https://twitter.com/foo/status/1789456123456789012")
    check(bool(match) and match.group(1) == "1789456123456789012", "推文 ID 提取")

    match = resolver.TWEET_ID_RE.search("https://x.com/i/web/status/1789456123456789")
    check(bool(match) and match.group(1) == "1789456123456789", "i/web 形式链接")

    # 非 X 的网页链接应该转交给 yt-dlp，而不是被直接拒掉
    media = resolver.resolve("https://www.bilibili.com/video/BV1GJ411x7h7")
    check(media.via == "ytdlp", "非 X 链接交给 yt-dlp", media.via)

    # 但任意域名不能混进推特下载链路（防 SSRF）
    try:
        resolver.assert_media_host("https://evil.example.com/a.mp4")
        check(False, "非 twimg 媒体域名应被拒绝")
    except resolver.ResolveError:
        check(True, "非 twimg 媒体域名被拒绝")

    try:
        resolver.resolve("今天天气不错")
        check(False, "无链接输入应报错")
    except resolver.ResolveError:
        check(True, "无链接输入报错")

    host_check = True
    try:
        resolver.assert_media_host("https://video.twimg.com/tweet_video/x.mp4")
    except resolver.ResolveError:
        host_check = False
    check(host_check, "video.twimg.com 在白名单内")


def test_transcode(tmp: str) -> None:
    src = os.path.join(tmp, "sample.mp4")
    if not make_sample(src):
        check(False, "合成测试视频失败（ffmpeg 无法编码）")
        return
    check(True, "合成测试视频", f"{os.path.getsize(src)} 字节")

    info = transcoder.probe(src)
    check(info.width == 640 and info.height == 360 and info.duration > 5, "ffprobe 读到尺寸与时长",
          f"{info.width}x{info.height} {info.duration:.2f}s {info.fps:.0f}fps")

    out = os.path.join(tmp, "out.gif")
    opts = transcoder.GifOptions(width=320, fps=12, max_bytes=5 * 1024 * 1024)
    result = transcoder.transcode_with_budget(src, out, opts, info, workdir=tmp)
    check(os.path.exists(out) and result["size"] > 1024, "生成 GIF", f"{result['size']} 字节")
    check(result["frames"] > 5, "帧数读取正常", f"{result['frames']} 帧")
    check(result["width"] == 320, "输出宽度按参数缩放到 320", f"{result['width']}x{result['height']}")
    check(result["size"] <= opts.max_bytes, "体积在预算内")

    # 故意给一个不可能的预算，验证多轮降级不会死循环、也不会抛异常
    tight = transcoder.GifOptions(width=480, fps=15, max_bytes=60 * 1024)
    result2 = transcoder.transcode_with_budget(
        src, os.path.join(tmp, "tight.gif"), tight, info, workdir=tmp, max_rounds=4
    )
    check(result2["round"] > 1 or result2["size"] <= tight.max_bytes,
          "预算过紧时触发多轮压缩", f"{result2['round']} 轮 / {result2['size']} 字节")
    check(result2["oversized"] is True, "压不下来时如实标记 oversized")

    # 截取片段的能力
    clip = transcoder.GifOptions(width=240, fps=8, duration=2.0, start=1.0, max_bytes=5 * 1024 * 1024)
    result3 = transcoder.transcode_with_budget(src, os.path.join(tmp, "clip.gif"), clip, info, workdir=tmp)
    check(1.0 <= result3["duration"] <= 3.2, "截取时长生效", f"{result3['duration']}s")


def test_web() -> None:
    os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="gifselftest-"))
    import app as web

    client = web.app.test_client()

    page = client.get("/")
    check(page.status_code == 200 and "简单gif".encode() in page.data, "首页可渲染")

    health = client.get("/api/health")
    check(health.status_code == 200 and health.get_json().get("ffmpeg") is True, "健康检查：ffmpeg 可用")

    profiles = client.get("/api/profiles")
    keys = (profiles.get_json() or {}).get("profiles", {})
    check(profiles.status_code == 200 and "emoji" in keys, "预设接口可用")

    empty = client.post("/api/jobs", json={"input": ""})
    check(empty.status_code == 400, "空输入被拒绝")

    missing = client.get("/api/jobs/aaaaaaaaaaaa")
    check(missing.status_code == 404, "未知任务返回 404")

    bad_static = client.get("/files/../../etc/passwd")
    check(bad_static.status_code == 404, "文件路径穿越被挡住")


def main() -> int:
    print("=" * 56)
    print("简单gif 自检")
    print("=" * 56)
    tmp = tempfile.mkdtemp(prefix="gifselftest-")
    try:
        test_resolver_rules()
        test_transcode(tmp)
        test_web()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print("-" * 56)
    print(f"通过 {passed}/{total}")
    for ok, label in results:
        if not ok:
            print(f"  未通过：{label}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
