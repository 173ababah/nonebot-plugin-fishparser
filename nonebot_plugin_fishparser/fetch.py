"""各站点取「标题 / 作者 / 时长 / 直链」的实现。零 LLM，只用 HTTP（+ 可选 yt-dlp）。"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

URL_RE = re.compile(r"https?://[^\s，,。、）)\]】\"'<>]+|(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+")
BILI_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10}|av\d{1,20})")


@dataclass
class ParseResult:
    title: str = ""
    author: str = ""
    duration: str = ""
    site: str = ""
    page_url: str = ""
    media_url: str = ""
    note: str = ""
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


async def _get_json(client: httpx.AsyncClient, url: str, **kw):
    r = await client.get(url, **kw)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


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


# ------------------------------------------------------------------ 其它站点（yt-dlp）
def _ytdlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


async def parse_ytdlp(url: str, cfg) -> ParseResult | None:
    """用 yt-dlp 只取元数据（--dump-single-json，不下载）。"""
    if not cfg.fishparser_ytdlp or not _ytdlp_available():
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-single-json", "--no-warnings", "--no-playlist",
            "--socket-timeout", "10", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), timeout=40)
        if proc.returncode != 0 or not out:
            return None
        d = json.loads(out.decode("utf-8", "ignore"))
    except Exception:
        return None
    return ParseResult(
        title=(d.get("title") or "").strip(),
        author=(d.get("uploader") or d.get("channel") or "").strip(),
        duration=_dur(d.get("duration")),
        site=(d.get("extractor_key") or d.get("extractor") or "").strip(),
        page_url=d.get("webpage_url") or url,
        media_url=d.get("url") or "",
        note="由 yt-dlp 解析（直链有时效，过期就重新解析）",
    )


async def parse_link(url: str, cfg) -> ParseResult | None:
    """统一入口：B 站走原生，其它走 yt-dlp（装了才走）。"""
    low = url.lower()
    if "b23.tv" in low or "bili2233.cn" in low:
        bv = await resolve_short(url, cfg)
        if bv:
            return await parse_bilibili(bv, cfg)
        return None
    if "bilibili.com" in low:
        m = BILI_BV_RE.search(url)
        if m:
            return await parse_bilibili(m.group(1), cfg)
        return None
    return await parse_ytdlp(url, cfg)
