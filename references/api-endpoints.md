# 端点速查与已知坑位

所有端点以 **当前 OpenAPI 为准**；下表是实测验证过的参数与行为，参数名可能随网关版本变化，执行前核对一次。

## 通用规则

- 路径必须以单个 `/api/` 开头，脚本会校验；
- 未知端点先查 OpenAPI（`https://api.tikhub.io/`）再动手，不靠猜；
- 请求头由脚本自动加 `Authorization: Bearer <Key>`，不要自己在参数里传 Key；
- 每个端点的**单价与每页条数不同**，直接决定成本，必须逐端点记录。

## 小红书（`/api/v1/xiaohongshu/app_v2/...`）

| 用途 | 路径 | 关键参数 | 备注 |
| --- | --- | --- | --- |
| 搜账号 | `search_users` | `keyword`, `page` | 同名账号极多，先列候选 |
| 搜内容 | `search_notes` | `keyword`, `page` | 返回条目外层是 `{model_type, note:{...}}` |
| 账号资料 | `get_user_info` | `user_id` | 拿真实粉丝数、简介、认证 |
| 账号作品 | `get_user_posted_notes` | `user_id`, `cursor` | 每页约 20–22 条，游标取本页最后一条的 `cursor` |
| 评论 | `get_note_comments` | `note_id`, `index`, `cursor`, `sort_strategy` | 见下方坑位① |
| 图文详情 | `get_image_note_detail` | `note_id` | 服务类笔记通常不带商品卡 |
| 视频详情 | `get_video_note_detail` | `note_id` | 同上 |

**内容条目关键字段**：`id`、`display_title`/`title`、`desc`、`time_desc`、`type`（`video`/`normal`）、`likes`、`comments_count`、`collected_count`、`share_count`、`is_goods_note`、`sticky`、`cursor`。

**搜索结果里的作者字段**：昵称在 `user.nickname`，ID 在 **`user.userid`**（不是 `user_id`）。要判断「低粉高数据」必须另调 `get_user_info` 取粉丝数。

## 抖音（`/api/v1/douyin/...`）

| 用途 | 路径 | 关键参数 | 备注 |
| --- | --- | --- | --- |
| 单条详情 | `/douyin/web/fetch_one_video` | `aweme_id` | 可取作者真实粉丝数 |
| 账号作品 | `/douyin/web/fetch_user_post_videos` | `sec_user_id`, `max_cursor`, `count` | 游标是数字，来自响应体 |
| 综合搜索 | `/douyin/search/fetch_general_search_v3` | `keyword`, `cursor`, `count` | 条目外层是 `{type, aweme_info:{...}}` |
| 视频评论 | `/douyin/web/fetch_video_comments` | `aweme_id`, `cursor`, `count` | 每页约 50 条 |

**内容条目关键字段**：`aweme_id`、`desc`、`create_time`（unix 时间戳）、`statistics.{digg_count, comment_count, collect_count, share_count, play_count}`、`is_top`、`author.follower_count`。

## 计费

| 用途 | 路径 | 参数 |
| --- | --- | --- |
| 官方价格计算 | `/api/v1/tikhub/user/calculate_price` | `endpoint`, `request_per_day` |

公开口径（仅用于理解量级，执行时重新核对）：多数端点约 `0.001 USD / 次`起，区间约 `0.001–0.01 USD / 次`，少数特殊端点更高；非 200 响应通常不收费；新账号一般有少量试用额度；日请求量增大可能触发阶梯折扣。

**请求数估算示例**：一个账号 100 条作品、作品列表每页 20 条、每条抓 1 页评论 → `1（资料）+ 5（列表）+ 100（评论）= 106 次`。若还要逐条调详情，再加 100 次。

---

## 已知坑位（实测，全部踩过）

### ① 评论排序参数不通用

传 `sort_strategy=like_count` 时，**部分笔记返回空的 comments 数组**（不是报错，是静默为空，极易误判成「这篇没有评论」）。

原因：该笔记只支持 `default` / `latest_v2`。稳妥做法是先用 `default`，需要按热度排再试 `like_count`，返回空就退回 `default`。

```bash
--params '{"note_id":"<id>","index":0,"sort_strategy":"default"}'
```

### ② 播放量字段不可用

抖音部分端点的 `play_count` 恒为 0，属接口限制、不是网络问题。**改用赞、评、藏、转做相对比较**，并在报告里写明该字段不可用。

### ③ 置顶内容会在每页重复出现

翻页时置顶作品每页都带一次，导致总条数虚增（曾出现 132 条原始 / 100 条唯一）。必须**按内容 ID 去重**；`collect_notes.py` 已内置。

### ④ 日期字段不完整 —— 必须用时间戳优先解析

`time_desc` 可能是 `07-06`（无年份）、`3 天前`、`Yesterday 13:45`。**但同一条数据的 `create_time` 是精确的 unix 时间戳**（秒级），优先解析它，文本日期只作兜底。

实战代价：曾有一个账号，因优先读 `time_desc`，被解析成 `2025-02-19 → 2025-11-15`，看起来像「已停更半年」；改用 `create_time` 后实际是 `2025-02-19 → 2026-09-08`（3 天前还在更新）。**整个账号活跃度判断被推翻。**

`normalize.py` 已按「时间戳优先」实现，并输出 `date_confidence` 标注。

**另一个稳妥判断**：账号作品列表按时间倒序，第一页第一条即最新发布，可用它交叉验证活跃度。

### ⑤ 搜索接口的粉丝数不可靠

搜索结果里的粉丝数可能是缓存值。判断「低粉高数据」必须另取账号资料（`get_user_info` / `fetch_one_video`）。`search_pick.py --enrich-author N` 会对互动最高的 N 条补齐，且同一作者只查一次。

### ⑥ 同名账号要先确认

按人名/昵称搜索会遇到大量同名号（曾一次搜出 20 个「Melody Zhang」）。正确流程：**列候选 → 取前 3 个资料（简介+粉丝）→ 请用户确认目标**，不要自行认领一个就开始批量抓。

### ⑦ 详情接口不一定返回商品卡

小红书服务类笔记（咨询、课程）的详情里可能没有 `goods_info`，说明成交发生在店铺或私域。此时**不要编造价格**，把客单价写成「评论区线索 + 待验证」。

### ⑧ 长批量容易被系统终止

一次跑几十次请求的长命令可能被环境终止（表现为进程被杀、无输出）。应对：**分批执行 + 支持 `--resume` 断点续跑 + 页数上限保护**，`collect_notes.py` 已内置。

### ⑨ 网络与代理

受限网络环境下可能需要绕过本机代理直连；反之若直连不通则走代理。脚本失败时会提示排查方向，不要反复重试同一个网络状态。

### ⑩ 端点路径不存在返回 404

不同版本端点前缀不同（`app_v2` vs `web`），404 说明路径错了，去 OpenAPI 核对，不要试着穷举。

---

## 成本控制实战建议

| 场景 | 建议 |
| --- | --- |
| 账号拆解 | 资料 1 次 + 全量列表（页数随粉丝量）+ 2–3 篇代表作评论，通常 10–30 次请求 |
| 关键词选题 | 搜索 N 页 + `--enrich-author` 补齐 Top 10，约 10–15 次 |
| 先摸底 | 先用 1 页确认字段，再决定是否拉全量 |
| 想省钱 | 评论只抓代表作，不要全量抓；详情只对已筛出的候选调 |
| 必备动作 | 全程挂 `--ledger`，事后可核对实际请求数 |
