"""离线 + 一条真实 B 站链接的自测。跑法：python tests/test_parser.py"""
import asyncio
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


def main():
    print("① 抠链接")
    ck("裸链接", F.pick_url("看这个 https://b23.tv/abc123 好笑") == "https://b23.tv/abc123")
    ck("无 scheme 补全", F.pick_url("b23.tv/xyz789") == "https://b23.tv/xyz789")
    ck("去掉尾部标点", F.pick_url("https://x.com/a。") == "https://x.com/a")
    ck("没有链接返回空", F.pick_url("今天天气不错") == "")
    ck("BV 号能抠出来", F.BILI_BV_RE.search("https://www.bilibili.com/video/BV1xx411c7mD").group(1) == "BV1xx411c7mD")

    print("② 时长格式化")
    ck("59 秒", F._dur(59) == "00:59", F._dur(59))
    ck("3 分 12 秒", F._dur(192) == "03:12", F._dur(192))
    ck("1 小时", F._dur(3725) == "1:02:05", F._dur(3725))
    ck("0 秒不显示", F._dur(0) == "")

    print("③ 真实 B 站解析（要联网）")
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

    print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    return 0 if not BAD else 1


if __name__ == "__main__":
    sys.exit(main())