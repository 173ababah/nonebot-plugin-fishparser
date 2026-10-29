"""nonebot-plugin-fishparser —— 万能链接解析（B站 / 抖音 / 小红书 / 油管 / 推特 …）

设计原则（按使用者的要求）：**零 LLM token**。全部是 HTTP + 正则 + 可选 yt-dlp，
不调任何大模型；解析结果就是「标题 / 作者 / 时长 / 直链」一张文字卡。

用法：
    /解析 <链接>        手动解析一条
    或在消息里直接贴链接（默认自动解析，可用 FISHPARSER_AUTO=0 关掉）

站点：
    · B 站（BV号 / av号 / b23.tv 短链）—— 原生接口，不下载、只给直链
    · 推特 / X                —— 原生：api.fxtwitter.com（纯文字推文也认，不用 yt-dlp）
    · 其它任何网页            —— OG / meta 兜底（标题 + 简介 + 封面）
    · 抖音 / 小红书 / 快手 / 微博 / 油管 … —— 装了 yt-dlp 能拿直链，没装就走 OG
      `pip install nonebot-plugin-fishparser[ytdlp]`
"""
from __future__ import annotations

import re

from nonebot import get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .config import Config
from .fetch import ParseResult, media_summary, parse_link, pick_url

__plugin_meta__ = PluginMetadata(
    name="鱼式链接解析",
    description="贴链接就出标题/作者/时长/直链，零 LLM 开销（B站原生 + 可选 yt-dlp）",
    usage="/解析 <链接>；或在消息里直接贴链接",
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

# nonebot 2.1 / 2.5 都能用：老版本没有 get_plugin_config
def _load_config():
    cfg = get_driver().config
    dump = getattr(cfg, "model_dump", None)
    return Config(**(dump() if callable(dump) else cfg.dict()))
plugin_config = _load_config()
_rate: dict[str, list[float]] = {}


def _chat_key(event: MessageEvent) -> str:
    if getattr(event, "group_id", None):
        return "group:%s" % event.group_id
    return "private:%s" % event.user_id


def _rate_ok(event: MessageEvent) -> bool:
    import time

    if plugin_config.fishparser_per_min <= 0:
        return True
    key = _chat_key(event)
    now = time.time()
    hits = [t for t in _rate.get(key, []) if now - t < 60]
    if len(hits) >= plugin_config.fishparser_per_min:
        _rate[key] = hits
        return False
    hits.append(now)
    _rate[key] = hits
    return True


def _has_link(text: str) -> bool:
    return bool(re.search(r"https?://|www\.", text or ""))


async def _rule_auto(event: MessageEvent) -> bool:
    if not plugin_config.fishparser_enabled or not plugin_config.fishparser_auto:
        return False
    return _has_link(event.get_plaintext() or "")


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _fmt(rs: ParseResult) -> str:
    lines = []
    if rs.title:
        lines.append("【%s】" % _clip(rs.title, 46))
    if getattr(rs, "body", ""):
        lines.append(_clip(rs.body, 150))
    meta = []
    if rs.author:
        meta.append(_clip(rs.author, 22))
    if rs.duration:
        meta.append(rs.duration)
    if rs.site:
        meta.append(rs.site)
    stats = (rs.extra or {}).get("stats")
    if stats:
        meta.append(stats)
    if meta:
        lines.append("  ·  ".join(meta))
    ms = media_summary((rs.extra or {}).get("medias") or [])
    if ms:
        lines.append("媒体：" + ms)
    if rs.media_url:
        lines.append("直链：%s" % rs.media_url)
    elif rs.page_url:
        lines.append(rs.page_url)
    cover = (rs.extra or {}).get("cover")
    if cover and cover != rs.media_url:
        lines.append("封面：%s" % cover)
    if rs.note:
        lines.append(rs.note)
    return "\n".join(lines)


manual_cmd = on_command("解析", aliases={"parse", "链接解析"}, priority=12, block=True)


@manual_cmd.handle()
async def _do_manual(bot: Bot, event: MessageEvent):
    if not plugin_config.fishparser_enabled:
        await manual_cmd.finish("解析功能关着呢。")
    url = pick_url(event.get_plaintext() or "")
    if not url:
        await manual_cmd.finish("给我个链接，比如 /解析 https://b23.tv/xxxx")
    if not _rate_ok(event):
        await manual_cmd.finish("别刷，一分钟最多 %d 次。" % plugin_config.fishparser_per_min)
    rs = await parse_link(url, plugin_config)
    if not rs:
        await manual_cmd.finish(
            "这个链接我解析不了（私密 / 要登录 / 页面里没有可解析的信息）。\n"
            "稳的：B站、推特（x.com）。\n"
            "其它的：抖音 / 小红书 / 快手 / 微博 / 油管 —— 服务器装了 yt-dlp 才行，"
            "没装就只能读到网页标题和封面。")
    await manual_cmd.finish(_fmt(rs))


auto_matcher = on_message(rule=Rule(_rule_auto), priority=15, block=False)


@auto_matcher.handle()
async def _do_auto(bot: Bot, event: MessageEvent):
    url = pick_url(event.get_plaintext() or "")
    if not url or not _rate_ok(event):
        return
    rs = await parse_link(url, plugin_config)
    if not rs:
        return
    logger.info("[fishparser] 解析成功 %s -> %s" % (rs.site or "?", rs.title[:40]))
    await auto_matcher.send(MessageSegment.text(_fmt(rs)))
