"""各站点取「标题 / 作者 / 时长 / 直链」的实现。零 LLM，只用 HTTP（+ 可选 yt-dlp）。

分工：
    B 站        —— 官方接口（带 buvid 反 412），零依赖
    推特 / X    —— api.fxtwitter.com（国内能通），纯文字推文也认，零依赖
    其它站点    —— 先 OG / meta 兜底（任何网页都能拿标题+简介+封面），最后 yt-dlp
"""
from __future__ import annotations

import asyncio
import html as html_mod
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

URL_RE = re.compile(r"https?://[^\s，,。、）)\]】\"'<>]+|(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+")
BILI_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10}|av\d{1,20})")

# 推特：短链/镜像域名都算，只要 URL 里有 /status/<id>
TW_HOSTS = ("x.com", "twitter.com", "vxtwitter.com", "fxtwitter.com",
            "fixupx.com", "fixvx.com", "twittpr.com")
TW_ID_RE = re.compile(r"/status(?:es)?/(\d{1,25})")   # 老的推文 id 只有两位（如 /status/20）
# fxtwitter 国内直连（实测腾讯云能通）；vxtwitter 有时被 Cloudflare 拦，做备用
TW_APIS = ("https://api.fxtwitter.com/status/%s",
           "https://api.vxtwitter.com/i/status/%s")

# 这些站 yt-dlp 比 OG 更值得先试（能拿到真直链）
YTDLP_HOSTS = ("douyin.com", "iesdouyin.com", "kuaishou.com", "xiaohongshu.com",
               "xhslink.com", "youtube.com", "youtu.be", "tiktok.com", "weibo.com",
               "weibo.cn", "acfun.cn", "vimeo.com", "ixigua.com", "bilibili.com")

_HTML_LIMIT = 400_000          # 只读网页前 400KB，够 <head> 里的 meta 了


@dataclass
class ParseResult:
    title: str = ""
    author: str = ""
    duration: str = ""
    site: str = ""
    page_url: str = ""
    media_url: str = ""
    note: str = ""
    body: str = ""              # 正文/简介（推特正文、网页 description）
    extra: dict = field(default_factory=dict)


def pick_url(text: str) -> str:
    """从一段话里抠出第一个 URL（没有 scheme 的 b23.tv 也补上）。"""
    m = URL_RE.search(text or "")
    if not m:
        return ""
    url = m.group(0).rstrip(".,;:!?)】』\"'")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def _dur(sec) -> str:
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return ""
    if sec <= 0:
        return ""
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def _num(v) -> str:
    """12345 -> 1.2万（推特那些大数看着舒服点）。"""
    try:
        v = int(float(v or 0))
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    if v >= 100000000:
        return "%.1f亿" % (v / 1e8)
    if v >= 10000:
        return "%.1f万" % (v / 1e4)
    return str(v)


def _oneline(s: str, limit: int = 0) -> str:
    """把多行文本压成一行，可选截断。"""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    if limit and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower()
    except Exception:
        return ""


async def _get_json(client: httpx.AsyncClient, url: str, **kw):
    r = await client.get(url, **kw)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


async def _get_text(client: httpx.AsyncClient, url: str, limit: int = _HTML_LIMIT) -> str:
    """只读网页前面一小段（meta 都在 <head>），别把整页几个 MB 拉下来。"""
    try:
        async with client.stream("GET", url) as r:
            if r.status_code >= 400:
                return ""
            chunks, size = [], 0
            async for b in r.aiter_bytes():
                chunks.append(b)
                size += len(b)
                if size >= limit:
                    break
            raw = b"".join(chunks)
            enc = (r.encoding or "").lower()
    except Exception:
        return ""
    for e in (enc, "utf-8", "gb18030"):
        if not e:
            continue
        try:
            return raw.decode(e)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


# ------------------------------------------------------------------ B 站（原生，不下载）
async def _bili_cookies(client: httpx.AsyncClient) -> dict:
    """B 站 412 风控：先去首页领 buvid3/buvid4，再带着这两颗 cookie 请求接口。

    云服务器 IP 经常被 B 站风控（接口直接 412 + 一段 HTML），裸请求拿不到数据；
    领个 buvid 就能过。这个坑在 fishbot 主仓库里踩过一次（§7-53）。
    """
    try:
        r = await client.get("https://www.bilibili.com/", headers={"User-Agent": UA})
        jar = {}
        for k in ("buvid3", "buvid4", "b_nut"):
            v = r.cookies.get(k)
            if v:
                jar[k] = v
        return jar
    except Exception:
        return {}


async def parse_bilibili(short: str, cfg) -> ParseResult | None:
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    async with httpx.AsyncClient(timeout=cfg.fishparser_timeout, headers=headers,
                                 follow_redirects=True) as c:
        v = await _get_json(c, "https://api.bilibili.com/x/web-interface/view",
                            params={"bvid": short})
        data = ((v or {}).get("data") or {}) if (v or {}).get("code") == 0 else {}
        if not data:                                  # 412 / -352 → 领 cookie 再来一次
            jar = await _bili_cookies(c)
            if jar:
                v = await _get_json(c, "https://api.bilibili.com/x/web-interface/view",
                                    params={"bvid": short}, cookies=jar)
                data = ((v or {}).get("data") or {}) if (v or {}).get("code") == 0 else {}
        if not data:
            return None
        rs = ParseResult(
            title=(data.get("title") or "").strip(),
            author=((data.get("owner") or {}).get("name") or "").strip(),
            duration=_dur(data.get("duration")),
            site="B站",
            page_url="https://www.bilibili.com/video/" + short,
            body=_oneline(data.get("desc") or "", 120),
        )
        cid = data.get("cid")
        if cid:
            _jar = await _bili_cookies(c)
            pd = await _get_json(c, "https://api.bilibili.com/x/player/playurl", cookies=_jar or None,
                                 params={"bvid": short, "cid": cid,
                                         "qn": cfg.fishparser_bili_qn,
                                         "fnval": 1, "platform": "html5",
                                         "high_quality": 1})
            durl = ((pd or {}).get("data") or {}).get("durl") or []
            if durl:
                rs.media_url = durl[0].get("url") or ""
                rs.note = "直链需带 Referer 才能下载（浏览器能直接打开）"
        return rs


async def resolve_short(url: str, cfg) -> str:
    """b23.tv / bili2233.cn 短链 → BV 号。"""
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    try:
        async with httpx.AsyncClient(timeout=cfg.fishparser_timeout, headers=headers,
                                     follow_redirects=True) as c:
            r = await c.get(url)
        m = BILI_BV_RE.search(str(r.url))
        return m.group(1) if m else ""
    except Exception:
        return ""


# ------------------------------------------------------------------ 推特 / X（原生）
def twitter_id(url: str) -> str:
    """x.com / twitter.com / fxtwitter 镜像 → 推文 id（不是推文链接返回空）。"""
    host = _host(url)
    if not host or not any(host == h or host.endswith("." + h) for h in TW_HOSTS):
        return ""
    m = TW_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _fx_medias(t: dict) -> list:
    """fxtwitter 的 media 结构：{all:[{type,url,thumbnail_url,duration}]}（老版本是 list）。"""
    m = t.get("media")
    if isinstance(m, dict):
        items = m.get("all")
        if not isinstance(items, list):
            items = list(m.get("videos") or []) + list(m.get("photos") or [])
    elif isinstance(m, list):
        items = m
    else:
        items = []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = (it.get("type") or "").lower()
        if kind not in ("photo", "video", "gif"):
            kind = "video" if (it.get("video_info") or it.get("duration")) else "photo"
        out.append({
            "type": kind,
            "url": it.get("url") or "",
            "thumb": it.get("thumbnail_url") or "",
            "duration": it.get("duration") or 0,
        })
    return out


def media_summary(medias: list) -> str:
    """图片×2 · 视频×1 00:42"""
    vids = [m for m in (medias or []) if m.get("type") in ("video", "gif")]
    pics = [m for m in (medias or []) if m.get("type") == "photo"]
    bits = []
    if pics:
        bits.append("图×%d" % len(pics))
    if vids:
        d = _dur(vids[0].get("duration"))
        bits.append("视频×%d%s" % (len(vids), (" " + d) if d else ""))
    return " · ".join(bits)


def _tw_from_fx(d) -> ParseResult | None:
    """fxtwitter JSON → ParseResult（纯函数，好测）。"""
    if not isinstance(d, dict):
        return None
    t = d.get("tweet")
    if not isinstance(t, dict):
        return None
    text = (t.get("text") or "").strip()
    a = t.get("author") or {}
    name = (a.get("name") or "").strip()
    sn = (a.get("screen_name") or "").strip()
    medias = _fx_medias(t)
    vids = [m for m in medias if m.get("type") in ("video", "gif")]
    pics = [m for m in medias if m.get("type") == "photo"]

    page = t.get("url") or ""
    if not page and sn and t.get("id"):
        page = "https://x.com/%s/status/%s" % (sn, t["id"])

    stats = []
    for label, key in (("♥", "likes"), ("↻", "retweets"), ("💬", "replies")):
        n = _num(t.get(key))
        if n:
            stats.append(label + n)
    views = _num(t.get("views"))

    if vids:
        note = "视频直链走 video.twimg.com，国内要梯子"
    elif pics:
        note = "图片在 x.com 原帖（pbs.twimg.com 国内直连不通）"
    else:
        note = ""

    return ParseResult(
        title="推特 · @" + sn if sn else "推特",
        body=text,
        author=(name + (" @" + sn if sn else "")) if name else ("@" + sn if sn else ""),
        duration=_dur(vids[0].get("duration")) if vids else "",
        site="推特",
        page_url=page,
        media_url=vids[0]["url"] if vids else "",
        note=note,
        extra={"stats": " ".join(stats) + ((" · %s 阅读" % views) if views else ""),
               "medias": medias},
    )


def _tw_from_vx(d) -> ParseResult | None:
    """vxtwitter JSON → ParseResult（备用通道，字段名都换了）。"""
    if not isinstance(d, dict) or not d.get("text"):
        return None
    medias = []
    for it in (d.get("media_extended") or []):
        if not isinstance(it, dict):
            continue
        kind = (it.get("type") or "").lower()
        dur = (it.get("duration_millis") or 0) / 1000.0
        medias.append({"type": "video" if kind in ("video", "gif") else "photo",
                       "url": it.get("url") or "", "thumb": it.get("thumbnail_url") or "",
                       "duration": dur})
    vids = [m for m in medias if m["type"] == "video"]
    name = (d.get("user_name") or "").strip()
    sn = (d.get("user_screen_name") or "").strip()
    stats = []
    for label, key in (("♥", "likes"), ("↻", "retweets"), ("💬", "replies")):
        n = _num(d.get(key))
        if n:
            stats.append(label + n)
    return ParseResult(
        title="推特 · @" + sn if sn else "推特",
        body=(d.get("text") or "").strip(),
        author=(name + (" @" + sn if sn else "")) if name else ("@" + sn if sn else ""),
        duration=_dur(vids[0]["duration"]) if vids else "",
        site="推特",
        page_url=d.get("tweetURL") or "",
        media_url=vids[0]["url"] if vids else "",
        note="视频直链走 video.twimg.com，国内要梯子" if vids else "",
        extra={"stats": " ".join(stats), "medias": medias},
    )


async def parse_twitter(url: str, cfg) -> ParseResult | None:
    tid = twitter_id(url)
    if not tid:
        return None
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Accept-Language": "en-US,en;q=0.9"}
    # 中转优先（国内服务器直连 fxtwitter 会被 Cloudflare 拦），见 config.fishparser_tw_proxy
    proxy = (getattr(cfg, "fishparser_tw_proxy", "") or "").strip()
    urls = ([proxy + tid] if proxy else []) + [api % tid for api in TW_APIS]
    async with httpx.AsyncClient(timeout=cfg.fishparser_timeout, headers=headers,
                                 follow_redirects=True) as c:
        for u in urls:
            for _ in range(2):          # Cloudflare 的挑战是抽风式的，重试一次
                d = await _get_json(c, u)
                rs = _tw_from_fx(d) or _tw_from_vx(d)
                if rs:
                    return rs
                if d is not None:
                    break
    return None


def tw_unavailable(url: str) -> ParseResult:
    """推特取不到内容时，也别只说"解析不了" —— 给个能点的镜像地址。"""
    tid = twitter_id(url)
    page = "https://fxtwitter.com/i/status/%s" % tid if tid else url
    return ParseResult(
        title="推特 · 没取到内容",
        body="服务器在国内，直连 x.com 被墙；公共解析接口这次也被 Cloudflare 拦了。",
        site="推特",
        page_url=page,
        note="点上面的链接（或把 x.com 换成 fxtwitter.com）在浏览器里看原帖",
    )


# ------------------------------------------------------------------ 通用网页（OG / meta）
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def metas(html_text: str) -> dict:
    """把 <head> 里的 meta 收成 {property: content}（不引第三方解析器）。"""
    out = {}
    for tag in _META_TAG_RE.findall(html_text or ""):
        attrs = {}
        for m in _META_ATTR_RE.finditer(tag):
            v = m.group(2) if m.group(2) is not None else m.group(3)
            attrs[m.group(1).lower()] = html_mod.unescape(v or "")
        key = (attrs.get("property") or attrs.get("name") or "").lower().strip()
        val = (attrs.get("content") or "").strip()
        if key and val and key not in out:
            out[key] = val
    return out


def og_from_html(html_text: str, url: str = "") -> ParseResult | None:
    """任何带 OG / description 的网页 → 标题 + 简介 + 封面（纯函数，好测）。"""
    metas_ = metas(html_text)
    title = (metas_.get("og:title") or metas_.get("twitter:title") or "").strip()
    if not title:
        m = _TITLE_RE.search(html_text or "")
        title = html_mod.unescape(m.group(1)).strip() if m else ""
    desc = (_oneline(metas_.get("og:description") or metas_.get("description")
                     or metas_.get("twitter:description") or "", 200))
    if not title and not desc:
        return None

    video = (metas_.get("og:video:url") or metas_.get("og:video:secure_url")
             or metas_.get("og:video") or "")
    image = metas_.get("og:image") or metas_.get("og:image:url") or metas_.get("twitter:image") or ""
    site = (metas_.get("og:site_name") or "").strip() or _host(url)
    rs = ParseResult(
        title=_oneline(title, 60),
        body=desc,
        site=_oneline(site, 14),
        page_url=url,
    )
    if image:
        rs.extra["cover"] = urljoin(url, image) if url else image
    if video:
        rs.media_url = urljoin(url, video) if url else video
        rs.note = "网页里的视频直链（有时效，过期重解析）"
    d = metas_.get("og:video:duration") or metas_.get("video:duration")
    if d:
        rs.duration = _dur(d)
    return rs


async def parse_og(url: str, cfg) -> ParseResult | None:
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    async with httpx.AsyncClient(timeout=cfg.fishparser_timeout, headers=headers,
                                 follow_redirects=True) as c:
        html_text = await _get_text(c, url)
    if not html_text:
        return None
    return og_from_html(html_text, url)


# ------------------------------------------------------------------ 兜底（yt-dlp）
def _ytdlp_cmd() -> list:
    """yt-dlp 的命令行：先找 PATH，再找当前解释器里的模块。

    systemd 起的进程 PATH 里**没有** `/opt/qqbot/.venv/bin`，只判断 `which yt-dlp`
    会一直以为没装（装了也不生效）。所以补一条 `python -m yt_dlp` 的路。
    """
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    try:
        if importlib.util.find_spec("yt_dlp") is not None:
            return [sys.executable, "-m", "yt_dlp"]
    except (ImportError, ValueError):
        pass
    return []


def _ytdlp_available() -> bool:
    return bool(_ytdlp_cmd())


async def parse_ytdlp(url: str, cfg) -> ParseResult | None:
    """用 yt-dlp 只取元数据（--dump-single-json，不下载）。"""
    cmd = _ytdlp_cmd() if getattr(cfg, "fishparser_ytdlp", True) else []
    if not cmd:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, "--dump-single-json", "--no-warnings", "--no-playlist",
            "--socket-timeout", "10", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
        if proc.returncode != 0 or not out:
            return None
        d = json.loads(out.decode("utf-8", "ignore"))
    except Exception:
        return None
    return ParseResult(
        title=_oneline((d.get("title") or "").strip(), 60),
        author=_oneline((d.get("uploader") or d.get("channel") or "").strip(), 18),
        duration=_dur(d.get("duration")),
        site=(d.get("extractor_key") or d.get("extractor") or "").strip(),
        page_url=d.get("webpage_url") or url,
        media_url=d.get("url") or "",
        note="由 yt-dlp 解析（直链有时效，过期就重新解析）",
    )


def _is_video_host(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in YTDLP_HOSTS)


async def parse_link(url: str, cfg) -> ParseResult | None:
    """统一入口：B站原生 → 推特原生 → 视频站 yt-dlp → OG 兜底 → yt-dlp 兜底。"""
    low = (url or "").lower()

    if "b23.tv" in low or "bili2233.cn" in low:
        bv = await resolve_short(url, cfg)
        if bv:
            rs = await parse_bilibili(bv, cfg)
            if rs:
                return rs
    elif "bilibili.com" in low:
        m = BILI_BV_RE.search(url)
        if m:
            rs = await parse_bilibili(m.group(1), cfg)
            if rs:
                return rs

    if twitter_id(url):
        # 推特专用：直连/中转都取不到时，也回一条「为什么 + 镜像地址」，别只说解析不了
        return await parse_twitter(url, cfg) or tw_unavailable(url)

    if _is_video_host(url):
        rs = await parse_ytdlp(url, cfg)
        if rs:
            return rs

    rs = await parse_og(url, cfg)
    if rs:
        return rs

    return await parse_ytdlp(url, cfg)
