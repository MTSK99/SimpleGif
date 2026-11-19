"""从 assets 里的 SVG 生成所有尺寸的图标。

    python tools/make_icons.py

设计源：
    assets/icon.svg        浅色模式（白底黑图形）——网页 favicon、PWA、占位
    assets/icon-dark.svg   深色模式（黑底白图形）——深色模式 favicon
    Android 的自适应图标不走这里，用 vector + values-night 让系统自己反转

用系统 Chrome 无头模式把 SVG 渲染成 PNG，保证各端视觉一致。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
STATIC = os.path.join(ROOT, "static")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if os.path.isfile(path):
            return path
    found = shutil.which("chrome") or shutil.which("chromium") or shutil.which("google-chrome")
    if found:
        return found
    raise SystemExit("找不到 Chrome / Edge，无法渲染 SVG。")


def render_svg(chrome: str, svg_path: str, size: int, out_path: str) -> Image.Image:
    """渲染成 size*2 的 PNG 再缩小，边缘更干净。"""
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            "--force-device-scale-factor=2",
            "--user-data-dir=" + os.path.join(tmp, "profile"),
            "--screenshot=" + out_path,
            "file:///" + svg_path.replace("\\", "/"),
        ]
        subprocess.run(args, timeout=90, capture_output=True)

    if not os.path.isfile(out_path):
        raise SystemExit(f"渲染失败：{svg_path}")

    img = Image.open(out_path).convert("RGBA")
    # deviceScaleFactor=2 时输出是 2 倍尺寸，等比缩回（不要裁剪，否则只剩左上角）
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    return img


def emit(chrome: str, svg_name: str, tmp_png: str) -> None:
    """把一份 SVG 导出成 512 / 192 两个 PNG，并复制 SVG 本身。"""
    stem = os.path.splitext(svg_name)[0]
    svg_path = os.path.join(ASSETS, svg_name)

    big = render_svg(chrome, svg_path, 512, tmp_png)
    big.save(os.path.join(STATIC, f"{stem}-512.png"), "PNG", optimize=True)
    print(f"wrote static/{stem}-512.png")

    small = big.resize((192, 192), Image.LANCZOS)
    small.save(os.path.join(STATIC, f"{stem}-192.png"), "PNG", optimize=True)
    print(f"wrote static/{stem}-192.png")

    shutil.copyfile(svg_path, os.path.join(STATIC, svg_name))
    print(f"wrote static/{svg_name}")


def main() -> None:
    chrome = find_chrome()
    print("使用:", chrome)
    tmp = os.path.join(ROOT, "_icon_tmp.png")
    try:
        emit(chrome, "icon.svg", tmp)
        emit(chrome, "icon-dark.svg", tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print("完成")


if __name__ == "__main__":
    main()
