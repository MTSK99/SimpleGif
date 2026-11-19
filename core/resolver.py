"""把用户粘贴的内容解析成可直接下载的媒体地址。

X（Twitter）上的"GIF"并不是 GIF 文件，而是 H.264 编码的 mp4，
路径形如 https://video.twimg.com/tweet_video/<hash>.mp4，
所以这里做的是：拿到 mp4 直链，交给后面的 ffmpeg 转成真正的 GIF。

解析顺序（任一层成功即返回，全部失败则要求用户直接贴 mp4 直链）：
    1. 文本里本身就带 video.twimg.com 的直链
    2. 第三方公开解析接口（默认 api.fxtwitter.com，可用环境变量覆盖）
    3. Twitter syndication 接口
    4. 推文页面 HTML 里的 meta / 正则

对外只依赖标准库 + requests，不绑定任何一家第三方服务的字段结构：
拿到的 JSON 会做深度遍历来搜集媒体地址，字段改名不会直接失效。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 允许下载的域名白名单，防止被当成任意 URL 代理（SSRF）。
# 注意这里是精确匹配 host，不做后缀匹配。
ALLOWED_HOSTS = {
    "video.twimg.com",
    "pbs.twimg.com",
    "ton.twimg.com",
    "abs.twimg.com",
}

# 允许访问的解析类域名（只发 GET，不下载大文件）
RESOLVER_HOSTS = {
    "api.fxtwitter.com",
    "api.vxtwitter.com",
    "cdn.syndication.twimg.com",
    "x.com",
    "twitter.com",
    "www.x.com",
    "mobile.x.com",
    "mobile.twitter.com",
    "fxtwitter.com",
}

TWEET_ID_RE = re.compile(
    r"(?:twitter\.com|x\.com)/(?:[A-Za-z0-9_]{1,20}|i/web|i)/status(?:es)?/(\d{1,25})",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s<>\"'\u3000-\u303f\uff00-\uffef]+", re.I)
MEDIA_RE = re.compile(
    r"https?://(?:video|pbs)\.twimg\.com/[^\s\"'<>\\]+?\.(?:mp4|m3u8|gif)(?:\?[^\s\"'<>\\]*)?",
    re.I,
)
# 从任意 JSON / HTML 文本里搜媒体地址，作为最后的兜底
LOOSE_MEDIA_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?\.(?:mp4|m3u8)(?:\?[^\s\"'<>\\]*)?",
    re.I,
)

MAX_BYTES_DEFAULT = 60 * 1024 * 1024


class ResolveError(Exception):
    """解析失败，message 直接面向用户展示。"""


@dataclass
class Media:
    """解析结果。"""

    media_url: str
    source_url: str
    kind: str = "video"  # gif / video / unknown
    hls: bool = False
    title: str = ""
    author: str = ""
    thumbnail: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    via: str = ""  # 命中的解析来源，便于排查
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "via": self.via,
            "hls": self.hls,
        }


def _resolver_bases() -> list[str]:
    """默认只放已验证可用的接口。

    api.vxtwitter.com 会返回 Cloudflare 的 "Just a moment..." 页面（403），
    所以不放在默认链里白等一轮超时；需要的话用 RESOLVER_API 显式指定。
    """
    raw = os.environ.get("RESOLVER_API", "").strip()
    if raw:
        return [b.strip().rstrip("/") for b in raw.split(",") if b.strip()]
    return ["https://api.fxtwitter.com"]


def _timeout() -> tuple[float, float]:
    try:
        value = float(os.environ.get("RESOLVE_TIMEOUT", "12"))
    except ValueError:
        value = 12.0
    return (5.0, value)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    # requests 默认 trust_env=True，会自动读取 HTTP(S)_PROXY，这里显式保持
    s.trust_env = True
    return s


def allowed_hosts() -> set[str]:
    extra = {
        h.strip().lower()
        for h in os.environ.get("ALLOW_EXTRA_HOSTS", "").split(",")
        if h.strip()
    }
    return ALLOWED_HOSTS | extra


def is_allowed_host(host: str | None) -> bool:
    return bool(host) and host.lower() in allowed_hosts()


def assert_media_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not is_allowed_host(host):
        raise ResolveError(
            f"不支持的媒体域名 {host or '(空)'}。本工具只处理 X/Twitter 的媒体直链。"
        )


def path_of(url: str) -> str:
    """取 URL 的路径部分。

    X 的媒体地址常带查询串（.../mEipTPWYPIwBGaZ_.mp4?tag=14），
    直接 endswith(".mp4") 会误判成非视频，必须只看 path。
    """
    try:
        return (urlparse(url).path or "").lower()
    except ValueError:
        return url.lower()


def _pixels_from_path(path: str) -> int:
    """从 .../avc1/720x720/xxx.mp4 这种路径里读出分辨率，用于优先选高清变体。"""
    match = re.search(r"/(\d{2,5})x(\d{2,5})/", path)
    if not match:
        return 0
    return int(match.group(1)) * int(match.group(2))


def _pick_best(urls: list[str]) -> str | None:
    """从候选地址里挑一个：优先 tweet_video（推文里的动图），同档取分辨率最高的 mp4。"""
    if not urls:
        return None
    uniq: list[str] = []
    for u in urls:
        if u not in uniq:
            uniq.append(u)

    def score(u: str) -> tuple[int, int]:
        path = path_of(u)
        is_mp4 = path.endswith(".mp4")
        is_m3u8 = path.endswith(".m3u8")
        if "tweet_video" in path and is_mp4:
            rank = 0
        elif is_mp4 and "amplify_video" in path:
            rank = 1
        elif is_mp4:
            rank = 2
        elif is_m3u8:
            rank = 3
        else:
            rank = 4
        return (rank, -_pixels_from_path(path))

    return sorted(uniq, key=score)[0]


def _kind_of(url: str) -> tuple[str, bool]:
    path = path_of(url)
    if "tweet_video" in path:
        return ("gif", False)
    if path.endswith(".m3u8"):
        return ("video", True)
    if path.endswith(".gif"):
        return ("gif", False)
    return ("video", False)


def _walk_media_urls(obj: Any, out: list[str], depth: int = 0) -> None:
    """深度遍历任意 JSON，收集所有看起来像媒体直链的字符串。"""
    if depth > 12:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_media_urls(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _walk_media_urls(v, out, depth + 1)
    elif isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http") and (".mp4" in s.lower() or ".m3u8" in s.lower()):
            out.append(s)


def _find_object_containing(obj: Any, target: str, depth: int = 0) -> dict | None:
    """找到包含目标地址的那个 dict，用来读取它的时长/尺寸等元信息。"""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, str) and v.strip() == target:
                return obj
        for v in obj.values():
            found = _find_object_containing(v, target, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_object_containing(v, target, depth + 1)
            if found is not None:
                return found
    return None


def _coerce_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _enrich(media: Media, payload: Any) -> None:
    """尽力补全元信息，取不到就保持默认值，绝不猜测。"""
    obj = _find_object_containing(payload, media.media_url)
    if isinstance(obj, dict):
        media.width = _coerce_int(obj.get("width")) or media.width
        media.height = _coerce_int(obj.get("height")) or media.height
        media.duration = _coerce_float(obj.get("duration")) or media.duration
        thumb = obj.get("thumbnail_url") or obj.get("thumbnail") or obj.get("poster")
        if isinstance(thumb, str) and thumb.startswith("http"):
            media.thumbnail = thumb
        t = obj.get("type")
        if isinstance(t, str) and t.lower() in {"gif", "video", "photo"}:
            media.kind = "gif" if t.lower() == "gif" else media.kind

    if isinstance(payload, dict):
        tweet = payload.get("tweet")
        if not isinstance(tweet, dict):
            tweet = payload
        text = tweet.get("text") or tweet.get("full_text")
        if isinstance(text, str) and text.strip():
            media.title = " ".join(text.split())[:160]
        author = tweet.get("author")
        if isinstance(author, dict):
            screen = author.get("screen_name") or author.get("username")
            name = author.get("name")
            if isinstance(name, str) and name.strip():
                media.author = name.strip()
            elif isinstance(screen, str):
                media.author = screen


def _try_json_api(session: requests.Session, tweet_id: str, source_url: str) -> Media | None:
    for base in _resolver_bases():
        url = f"{base}/status/{tweet_id}"
        try:
            resp = session.get(url, timeout=_timeout())
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (None, 200, "200"):
                continue
        urls: list[str] = []
        _walk_media_urls(payload, urls)
        best = _pick_best(urls)
        if not best:
            continue
        if not path_of(best).endswith((".mp4", ".m3u8")):
            continue
        kind, hls = _kind_of(best)
        media = Media(media_url=best, source_url=source_url, kind=kind, hls=hls, via=urlparse(base).hostname or base)
        _enrich(media, payload)
        return media
    return None


def _try_syndication(session: requests.Session, tweet_id: str, source_url: str) -> Media | None:
    """Twitter 自家的 syndication 返回结构较稳定，但可能因风控不可用，纯兜底。"""
    for token in ("0", "a"):
        url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token={token}"
        try:
            resp = session.get(url, timeout=_timeout())
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        urls: list[str] = []
        _walk_media_urls(payload, urls)
        best = _pick_best(urls)
        if not best:
            continue
        kind, hls = _kind_of(best)
        media = Media(media_url=best, source_url=source_url, kind=kind, hls=hls, via="syndication")
        _enrich(media, payload)
        return media
    return None


def _try_html(session: requests.Session, tweet_id: str, source_url: str) -> Media | None:
    for page in (f"https://x.com/i/status/{tweet_id}", f"https://twitter.com/i/status/{tweet_id}"):
        try:
            resp = session.get(page, timeout=_timeout())
        except requests.RequestException:
            continue
        if resp.status_code >= 400:
            continue
        text = resp.text
        candidates = [m.group(0) for m in MEDIA_RE.finditer(text)]
        if not candidates:
            candidates = [m.group(0) for m in LOOSE_MEDIA_RE.finditer(text) if "twimg.com" in m.group(0)]
        best = _pick_best(candidates)
        if not best:
            continue
        kind, hls = _kind_of(best)
        media = Media(media_url=best, source_url=source_url, kind=kind, hls=hls, via="html")
        og = re.search(r'property="og:(?:video|image)"\s+content="([^"]+)"', text)
        if og and og.group(1).startswith("http"):
            media.thumbnail = og.group(1)
        return media
    return None


def resolve(raw_input: str) -> Media:
    """把用户输入（推文链接 / 含链接的分享文本 / 媒体直链）解析成 Media。"""
    text = (raw_input or "").strip()
    if not text:
        raise ResolveError("请先粘贴推文链接或 GIF 直链。")

    # 1) 输入里直接带 twimg 直链
    direct = MEDIA_RE.search(text)
    if direct:
        url = direct.group(0)
        assert_media_host(url)
        kind, hls = _kind_of(url)
        return Media(media_url=url, source_url=url, kind=kind, hls=hls, via="direct")

    # 2) 推文链接
    id_match = TWEET_ID_RE.search(text)
    if not id_match:
        any_url = URL_RE.search(text)
        if any_url:
            candidate = any_url.group(0).rstrip(".,);]")
            host = (urlparse(candidate).hostname or "").lower()
            if is_allowed_host(host):
                assert_media_host(candidate)
                kind, hls = _kind_of(candidate)
                return Media(media_url=candidate, source_url=candidate, kind=kind, hls=hls, via="direct")
            if host in RESOLVER_HOSTS or host.endswith(("x.com", "twitter.com")):
                raise ResolveError(
                    "这是 X 的链接，但里面没有推文 ID。\n"
                    "需要形如 https://x.com/用户名/status/1234567890123456789 的链接——"
                    "打开具体那条推文，用「复制链接」拿到的才是这个。"
                )
            # 非 X 站点交给 yt-dlp，它覆盖上千个视频站
            from . import ytdlp

            if not ytdlp.is_available():
                raise ResolveError(
                    f"暂不支持 {host or candidate} 这个站点。\n"
                    "服务器装了 yt-dlp 之后就能解析 B 站、YouTube、微博这类视频链接：\n"
                    "pip install yt-dlp"
                )
            return Media(
                media_url=candidate,
                source_url=candidate,
                kind="video",
                via="ytdlp",
            )
        raise ResolveError(
            "没识别出链接。粘贴推文链接、或任意视频页面的地址都行。"
        )

    tweet_id = id_match.group(1)
    session = _session()
    errors: list[str] = []

    for name, fn in (
        ("解析接口", _try_json_api),
        ("syndication", _try_syndication),
        ("页面解析", _try_html),
    ):
        try:
            media = fn(session, tweet_id, id_match.group(0))
        except ResolveError:
            raise
        except Exception as exc:  # 单层失败不影响下一层
            errors.append(f"{name}: {exc}")
            continue
        if media:
            assert_media_host(media.media_url)
            return media
        errors.append(f"{name}: 未取到媒体地址")

    raise ResolveError(
        "没能从这条推文里取到视频地址（推文可能已删除、仅粉丝可见，或图片而不是动图）。\n"
        "可以打开推文，右键复制 video.twimg.com 开头的 mp4 直链后直接粘贴。"
        + ("\n\n排查信息：" + "；".join(errors) if os.environ.get("DEBUG_RESOLVE") else "")
    )
