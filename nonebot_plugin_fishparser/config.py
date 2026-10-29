"""配置项（全部 FISHPARSER_ 前缀，走 nonebot 的 .env）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Config(BaseModel):
    # 总开关
    fishparser_enabled: bool = Field(default=True, description="启用链接解析")
    # 消息里贴链接自动解析（关掉就只能用 /解析）
    fishparser_auto: bool = Field(default=True, description="自动解析消息里的链接")
    # 每个会话每分钟最多解析几次（防止刷屏/被打爆）
    fishparser_per_min: int = Field(default=3, description="每会话每分钟上限，0=不限")
    # B 站清晰度：32=360p（省流量，默认），80=1080p
    fishparser_bili_qn: int = Field(default=32, description="B站 qn：32=360p, 64=720p, 80=1080p")
    # 是否允许用 yt-dlp 解析非 B 站站点（没装 yt-dlp 会自动跳过）
    fishparser_ytdlp: bool = Field(default=True, description="用 yt-dlp 兜底其它站点")
    # 推特中转：国内服务器直连 api.fxtwitter.com 会被 Cloudflare 拦。
    # 填一个境外中转的前缀（会请求 <前缀><推文id>，返回 fxtwitter 同款 JSON），留空=直连
    fishparser_tw_proxy: str = Field(default="", description="推特中转前缀，如 https://xxx.pages.dev/tw/")
    # 请求超时（秒）
    fishparser_timeout: float = Field(default=12.0, description="单次请求超时（秒）")
