# nonebot-plugin-fishparser 🐟

> 贴个链接就出**标题 / 作者 / 时长 / 直链**，**不调用任何大模型**（零 token 开销）。

`astrbot_plugin_parser` 的 nonebot2 版实现，按「够用就好」重写：不下载视频、不转存、
只把能直接播的 mp4 直链和元信息给你，所以又快又不费钱。

## 支持

| 站点 | 方式 | 说明 |
|---|---|---|
| B 站（BV / av / b23.tv 短链） | 原生接口 | 免登录，给 360p mp4 直链（可配 720p/1080p）；直链要带 Referer 才能下载 |
| **推特 / X（x.com / twitter.com / fxtwitter / fixupx …）** | **原生**：`api.fxtwitter.com`（备用 `api.vxtwitter.com`） | **纯文字推文也能解析**（yt-dlp 对纯文字推文是无解的），出作者 / 正文 / 互动数 / 媒体清单 |
| 任何有 OG 标签的网页 | 原生：抓 `<meta>` | 标题 + 简介 + 封面，翻不了的站至少能读个大概 |
| 抖音 / 小红书 / 快手 / 微博 / 油管 / A站 … | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 可选依赖，装了才启用；只取元数据不下载 |

> **国内服务器注意（实测腾讯云）**：`x.com`、`pbs.twimg.com`、`video.twimg.com` 全部直连超时，
> `api.fxtwitter.com` 会被 Cloudflare 挑战（`cf-mitigated: challenge`）。
> 想在国内机器上稳定解析推特，需要在墙外放一个**中转**（Cloudflare Worker 20 行就够），
> 然后填 `FISHPARSER_TW_PROXY=https://你的中转/tw/`：插件会请求 `<中转><推文id>`，
> 中转把 `api.fxtwitter.com/status/<id>` 的 JSON 原样返回即可（字段不用改）。

## 安装

```bash
nb plugin install nonebot-plugin-fishparser
# 想要抖音/小红书/快手/微博这类站点（B站和推特不需要）：
pip install "nonebot-plugin-fishparser[ytdlp]"
```

> yt-dlp 装进 venv 后**不用**保证它在 PATH 里 —— 插件会退回 `python -m yt_dlp`
> （systemd 起的进程 PATH 里没有 `.venv/bin`，只判断 `which yt-dlp` 会一直以为没装）。

## 用法

```
/解析 https://b23.tv/xxxx         # 手动解析
/解析 https://x.com/xxx/status/123
<直接贴链接>                       # 自动解析（可关）
```

输出：

```
【【明日方舟】新版本前瞻】
明日方舟  ·  03:12  ·  B站
直链：https://upos-sz-mirrorcos.bilivideo.com/...

【推特 · @NASA】
Mars, but make it art.
NASA @NASA  ·  00:42  ·  推特  ·  ♥1.2万 ↻678 💬90
媒体：图×1 · 视频×1 00:42
```

## 配置（`.env`）

```ini
FISHPARSER_ENABLED=true      # 总开关
FISHPARSER_AUTO=true         # 消息里的链接自动解析
FISHPARSER_PER_MIN=3         # 每个会话每分钟上限，0=不限
FISHPARSER_BILI_QN=32        # B站清晰度：32=360p 64=720p 80=1080p
FISHPARSER_YTDLP=true        # 用 yt-dlp 兜底其它站点
FISHPARSER_TIMEOUT=12        # 请求超时（秒）
FISHPARSER_TW_PROXY=         # 推特中转前缀（墙外 Worker），留空=直连 fxtwitter
```

## 为什么省 token

整个插件没有任何模型调用：B 站走公开接口，推特走 fxtwitter 的 JSON 接口，
其它站点走 yt-dlp 的 `--dump-single-json` 或一次网页 GET。报文只有几百字节到几 KB。

## License

MIT © 173ababah
