"""离线 + 少量真实链接的自测。跑法：python tests/test_parser.py"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nonebot  # noqa: E402  （插件 __init__ 要驱动已初始化）

nonebot.init()
from nonebot_plugin_fishparser import fetch as F  # noqa: E402

OK, BAD = [], []


def ck(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  [OK]   %s" % name if cond else "  [FAIL] %s %s" % (name, extra))


class Cfg:
    fishparser_timeout = 12.0
    fishparser_bili_qn = 32
    fishparser_ytdlp = True


# fxtwitter 真实返回的精简版（带一张图 + 一个视频）
FX_SAMPLE = {
    "code": 200, "message": "OK",
    "tweet": {
        "url": "https://x.com/nasa/status/1234567890123456789",
        "id": "1234567890123456789",
        "text": "Mars, but make it art.\n\nA new image from the rover.",
        "author": {"name": "NASA", "screen_name": "NASA"},
        "likes": 12345, "retweets": 678, "replies": 90, "views": 4567890,
        "media": {"all": [
            {"type": "photo", "url": "https://pbs.twimg.com/media/a.jpg",
             "thumbnail_url": "https://pbs.twimg.com/media/a_small.jpg"},
            {"type": "video", "url": "https://video.twimg.com/ext_tw_video/1.mp4",
             "thumbnail_url": "https://pbs.twimg.com/media/b.jpg", "duration": 42.5},
        ]},
    },
}

VX_SAMPLE = {
    "text": "hello world", "user_name": "Someone", "user_screen_name": "someone",
    "tweetURL": "https://twitter.com/someone/status/20", "likes": 3, "retweets": 1,
    "replies": 0,
    "media_extended": [{"type": "image", "url": "https://pbs.twimg.com/media/z.jpg",
                        "thumbnail_url": ""}],
}

OG_SAMPLE = """<!doctype html><html><head>
<meta charset="utf-8">
<meta property="og:site_name" content="少数派">
<meta content="一篇文章的标题" property="og:title">
<meta property="og:description" content="这是 &amp; 一段简介，很长很长。">
<meta property="og:image" content="/img/cover.jpg">
<script>var a = 1;</script>
</head><body>正文</body></html>"""


def main():
    print("① 抠链接")
    ck("裸链接", F.pick_url("看这个 https://b23.tv/abc123 好笑") == "https://b23.tv/abc123")
    ck("无 scheme 补全", F.pick_url("b23.tv/xyz789") == "https://b23.tv/xyz789")
    ck("去掉尾部标点", F.pick_url("https://x.com/a。") == "https://x.com/a")
    ck("没有链接返回空", F.pick_url("今天天气不错") == "")
    ck("BV 号能抠出来", F.BILI_BV_RE.search("https://www.bilibili.com/video/BV1xx411c7mD").group(1) == "BV1xx411c7mD")

    print("② 时长 / 数字格式化")
    ck("59 秒", F._dur(59) == "00:59", F._dur(59))
    ck("3 分 12 秒", F._dur(192) == "03:12", F._dur(192))
    ck("1 小时", F._dur(3725) == "1:02:05", F._dur(3725))
    ck("0 秒不显示", F._dur(0) == "")
    ck("1.2万", F._num(12345) == "1.2万", F._num(12345))
    ck("亿", F._num(1234567890) == "12.3亿", F._num(1234567890))
    ck("0 不显示", F._num(0) == "")

    print("③ 推特链接识别")
    for u, want in [
        ("https://x.com/jack/status/20", "20"),
        ("https://twitter.com/jack/status/20?s=20", "20"),
        ("https://mobile.twitter.com/jack/statuses/20", "20"),
        ("https://x.com/i/web/status/20", "20"),
        ("https://fxtwitter.com/jack/status/20", "20"),
        ("https://fixupx.com/jack/status/20", "20"),
        ("https://x.com/jack", ""),
        ("https://example.com/status/20", ""),
        ("", ""),
    ]:
        ck("twitter_id(%s)" % (u or "空"), F.twitter_id(u) == want, F.twitter_id(u))

    print("④ fxtwitter JSON → 结果（离线）")
    rs = F._tw_from_fx(FX_SAMPLE)
    ck("拿到结果", rs is not None)
    if rs:
        ck("站点=推特", rs.site == "推特", rs.site)
        ck("作者带 handle", rs.author == "NASA @NASA", rs.author)
        ck("标题带 handle", "@NASA" in rs.title, rs.title)
        ck("正文保留换行", "rover" in rs.body)
        ck("视频时长 00:42", rs.duration == "00:42", rs.duration)
        ck("直链取视频", rs.media_url.endswith(".mp4"), rs.media_url)
        ck("媒体统计", F.media_summary(rs.extra["medias"]) == "图×1 · 视频×1 00:42",
           F.media_summary(rs.extra["medias"]))
        ck("互动数据", "♥1.2万" in rs.extra["stats"], rs.extra["stats"])
    ck("非推特 JSON 不误判", F._tw_from_fx({"code": 404, "message": "Not found"}) is None)

    print("⑤ vxtwitter JSON → 结果（离线）")
    vx = F._tw_from_vx(VX_SAMPLE)
    ck("拿到结果", vx is not None)
    if vx:
        ck("图片算媒体", F.media_summary(vx.extra["medias"]) == "图×1", F.media_summary(vx.extra["medias"]))
        ck("没有视频就没有直链", vx.media_url == "", vx.media_url)

    print("⑥ 通用网页 OG（离线）")
    meta = F.metas(OG_SAMPLE)
    ck("property 在前/在后都认", meta.get("og:title") == "一篇文章的标题", meta.get("og:title"))
    ck("实体还原", "&" in meta.get("og:description", ""), meta.get("og:description"))
    og = F.og_from_html(OG_SAMPLE, "https://sspai.com/post/1")
    ck("拿到标题", og and og.title == "一篇文章的标题")
    if og:
        ck("简介压成一行", "\n" not in og.body and og.body)
        ck("站名", og.site == "少数派", og.site)
        ck("相对封面补全", og.extra.get("cover") == "https://sspai.com/img/cover.jpg",
           og.extra.get("cover"))
    ck("空页面返回 None", F.og_from_html("<html><head></head><body>x</body></html>") is None)
    ck("<title> 兜底", F.og_from_html("<html><head><title>只有标题</title></head></html>").title == "只有标题")

    print("⑦ 真实 B 站解析（要联网）")
    try:
        rs = asyncio.run(F.parse_bilibili("BV1uExu6GEFs", Cfg()))
    except Exception as e:
        rs = None
        print("     网络异常：%r" % (e,))
    if rs:
        ck("拿到标题", bool(rs.title), rs.title[:30])
        ck("拿到时长", bool(rs.duration), rs.duration)
        ck("拿到 mp4 直链", rs.media_url.startswith("http"), rs.media_url[:40])
        ck("站点标了 B站", rs.site == "B站")
    else:
        print("  [SKIP] 没网/接口挂了，跳过")

    print("⑧ 真实推特解析（要联网 + fxtwitter 可达）")
    try:
        rs = asyncio.run(F.parse_link("https://x.com/jack/status/20", Cfg()))
    except Exception as e:
        rs = None
        print("     网络异常：%r" % (e,))
    if rs and rs.title.startswith("推特 · 没取到"):
        print("  [SKIP] fxtwitter 被 Cloudflare 拦/网不通，走了降级提示（国内网络正常现象）")
        print("         降级文案：%s / %s" % (rs.body[:30], rs.page_url))
    elif rs:
        ck("纯文字推文也能解析", rs.site == "推特" and "twttr" in rs.body, rs.body[:40])
        ck("原帖地址", "status/20" in rs.page_url, rs.page_url)
        ck("纯文字没有直链", rs.media_url == "", rs.media_url)
    else:
        print("  [SKIP] 没网，跳过")

    print("⑧b 推特取不到时的降级（离线）")
    fb = F.tw_unavailable("https://x.com/jack/status/20")
    ck("有解释", "被墙" in fb.body, fb.body[:30])
    ck("给镜像地址", fb.page_url == "https://fxtwitter.com/i/status/20", fb.page_url)
    ck("不是 None（不会只说解析不了）", fb.title.startswith("推特"), fb.title)

    print("⑧c yt-dlp 命令（离线）")
    cmd = F._ytdlp_cmd()
    ck("能定位到 yt-dlp（PATH 或 python -m）", bool(cmd), cmd)

    print("⑨ 真实网页 OG 兜底（要联网）")
    try:
        rs = asyncio.run(F.parse_og("https://www.bilibili.com/", Cfg()))
    except Exception as e:
        rs = None
        print("     网络异常：%r" % (e,))
    if rs:
        ck("网页也能拿到标题", bool(rs.title), rs.title[:30])
    else:
        print("  [SKIP] 没网，跳过")

    print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())
