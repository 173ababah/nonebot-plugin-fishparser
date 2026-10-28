# nonebot-plugin-fishparser 🐟

> 贴个链接就出**标题 / 作者 / 时长 / 直链**，**不调用任何大模型**（零 token 开销）。

`astrbot_plugin_parser` 的 nonebot2 版实现，按「够用就好」重写：不下载视频、不转存、
只把能直接播的 mp4 直链和元信息给你，所以又快又不费钱。

## 支持

| 站点 | 方式 | 说明 |
|---|---|---|
| B 站（BV / av / b23.tv 短链） | 原生接口 | 免登录，给 360p mp4 直链（可配 720p/1080p）；直链要带 Referer 才能下载 |
| 抖音 / 小红书 / 快手 / 微博 / 油管 / 推特 / A站 … | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 可选依赖，装了才启用；只取元数据不下载 |

## 安装

```bash
nb plugin install nonebot-plugin-fishparser
# 想要 B 站以外的站点：
pip install "nonebot-plugin-fishparser[ytdlp]"
```

## 用法

```
/解析 https://b23.tv/xxxx         # 手动解析
<直接贴链接>                       # 自动解析（可关）
```

输出：

```
【【明日方舟】新版本前瞻】
UP 明日方舟  ·  03:12  ·  B站
直链：https://upos-sz-mirrorcos.bilivideo.com/...
```

## 配置（`.env`）

```ini
FISHPARSER_ENABLED=true      # 总开关
FISHPARSER_AUTO=true         # 消息里的链接自动解析
FISHPARSER_PER_MIN=3         # 每个会话每分钟上限，0=不限
FISHPARSER_BILI_QN=32        # B站清晰度：32=360p 64=720p 80=1080p
FISHPARSER_YTDLP=true        # 用 yt-dlp 兜底其它站点
FISHPARSER_TIMEOUT=12        # 请求超时（秒）
```

## 为什么省 token

整个插件没有任何模型调用：B 站走公开接口，其它站点走 yt-dlp 的 `--dump-single-json`。
报文只有几百字节到几 KB。

## License

MIT © 173ababah
