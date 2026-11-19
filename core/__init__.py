"""简单gif —— 把 X/Twitter 的 GIF 转成微信能直接用的 GIF。

模块划分：
    resolver    链接 -> 媒体直链
    downloader  媒体直链 -> 本地文件
    transcoder  mp4 -> gif（两遍调色板 + 体积预算降级）
    jobs        后台任务与状态查询
"""

__version__ = "1.0.0"
