# 输出字段标准

原则：**先保存原始响应，再映射到统一字段**。平台没有的字段保持为空，不猜、不填 0 冒充数据。

## 任务总览（每次调研在报告开头写明）

| 字段 | 含义 |
| --- | --- |
| `research_goal` | 调研目标 |
| `platforms` | 涉及平台 |
| `sample_scope` | 账号、作品、评论与时间范围 |
| `endpoints` | 实际使用的端点 |
| `estimated_requests` | 执行前预估请求数 |
| `actual_requests` | 实际成功请求数（可从 ledger 统计） |
| `estimated_cost_usd` | 执行前预估费用 |
| `actual_cost_usd` | 可从网关账单核实的实际费用 |
| `price_checked_at` | 单价查询时间 |
| `price_source` | 端点文档或官方价格接口 |
| `collected_at` | 采集时间 |
| `missing_fields` | 本次缺失或不可用的字段 |
| `unverified_items` | 标注为「待验证」的推断项 |

## 账号表

| 字段 | 含义 |
| --- | --- |
| `platform` | 平台 |
| `account_id` | 平台账号 ID |
| `author_name` | 显示名称 |
| `profile_url` | 主页链接 |
| `bio` | 简介 |
| `followers` | 粉丝数 |
| `following` | 关注数 |
| `total_likes` | 累计获赞或平台对应指标 |
| `verified` | 认证信息 |
| `collected_at` | 采集时间 |
| `source_file` | 原始文件 |

## 作品表

`normalize.py` 默认输出以下字段：

| 字段 | 含义 |
| --- | --- |
| `platform` | 平台 |
| `content_id` | 内容 ID |
| `title` | 标题 |
| `caption` | 正文或文案 |
| `published_at` | 发布时间（见下方置信度） |
| `date_confidence` | 日期置信度：`exact` / `inferred_year` / `relative` / `unknown` |
| `media_type` | 视频 / 图文 |
| `views` | 播放或浏览（可能不可用） |
| `likes` | 点赞 |
| `comments` | 评论数 |
| `favorites` | 收藏数 |
| `shares` | 转发数 |
| `has_product` | 是否挂商品/转化位 |
| `pinned` | 是否置顶 |
| `collected_at` | 采集时间 |
| `source_file` | 来源原始文件 |
| `note_type_raw` | 平台原始类型值，便于回溯 |
| `published_at_raw` | 平台原始日期文本（如 `09-03`、`3d ago`），便于回溯 |
| `published_ts` | 平台原始时间戳，**优先用于精确解析** |

### 日期置信度（重要）

**解析优先级：平台时间戳 > 完整日期文本 > 不完整日期文本。**

实测教训：小红书的 `time_desc` 对近期内容只返回 `09-03` 或 `3d ago`，但同一条数据的 `create_time` 是精确 unix 时间戳。如果优先读文本字段，整个账号的时间线会错位（曾把一个 2026 年的账号判成 2025 年，并误判为「已停更」）。`normalize.py` 现已优先使用时间戳。

| 置信度 | 场景 | 使用方式 |
| --- | --- | --- |
| `exact` | 平台时间戳，或完整 `YYYY-MM-DD` | 可参与年度分布与时间趋势结论 |
| `inferred_year` | 只返回 `07-06` 这类月-日 | **不写年度结论**，只能同一年内排序 |
| `relative` | 「3 天前」「Yesterday」 | 只能判断近期活跃，不能定位具体日期 |
| `unknown` | 无法解析 | 人工核对 |

## 评论表

| 字段 | 含义 |
| --- | --- |
| `platform` | 平台 |
| `content_id` | 所属内容 ID |
| `source_url` | 所属内容链接 |
| `comment_id` | 评论 ID |
| `comment_text` | 评论正文 |
| `commented_at` | 评论时间 |
| `likes` | 评论点赞 |
| `parent_comment_id` | 父评论 ID（楼中楼） |
| `collected_at` | 采集时间 |
| `source_file` | 原始文件 |

评论分析按三类信号聚类，比按情绪分类更贴近变现判断：

- **需求信号**：求具体方案、求产品、问「在哪买」；
- **异议信号**：质疑、不信任、说贵、说没用；
- **转化信号**：已购、已报名、反馈效果、主动推荐。

## 推导字段

`topic`、`hook_type`、`audience_problem`、`content_structure`、`opportunity`、`customer_price_estimate` 等属于分析结论，**必须与原始字段分表存放**，并在报告中标注为推断；涉及金额、销量、转化率的推断一律标 **待验证**。

## 口径限制（写进每份报告）

- 不同平台的互动口径不一致（如抖音播放量与小红书浏览量的统计条件不同），**不要横向直接比较**；
- 部分端点的播放/浏览量字段恒为 0 或缺失，此时用赞、评、藏、转做相对比较，并说明字段不可用；
- 搜索结果里的粉丝数可能不是实时值，需要判断「低粉高数据」时必须另取账号资料；
- 一次性采集是快照，账号数据会变化，报告需注明采集时间。
