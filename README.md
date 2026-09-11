# 碗子调研 · wanzi-research

一个把「帮我看看这个博主」变成**可复现流水线**的 AI 技能：采集全网社媒公开数据 → 归档原始证据 → 按四维框架拆出结论。

适用于 WorkBuddy / Claude Code 等支持 Agent Skills 的环境。

## 它解决什么

大多数人对着一个账号翻一小时，还是说不清它「帮哪类人、解决什么问题、靠什么赚钱」。

这个技能把这件事拆成两步固定的动作：

1. **采集可核对的证据**——账号资料、全量作品、评论区、搜索结果，全部落到本地文件，原始响应不加工、不覆盖，逐页可回溯；
2. **按四维框架推导结论**——人设 / 人群 / 内容 / 商业，每个结论都能回指到具体原始文件，推断项强制标注「待验证」。

## 四个脚本（不是提示词，是真干活的工具）

| 脚本 | 干什么 | 解决的真实痛点 |
| --- | --- | --- |
| `api_request.py` | 密钥安全读取、脱敏预览、费用预估、请求落盘、请求记账 | Key 不外泄；花多少钱事先有数；失败自动退避重试 |
| `collect_notes.py` | 账号作品**全量翻页**采集 | 漏页、置顶重复导致条数虚增、长批量被中断后能 `--resume` 续跑 |
| `search_pick.py` | 关键词搜索 + 低粉高数据筛选 | 搜索结果的粉丝数不可靠，自动补齐作者真实粉丝并按效率分排序 |
| `normalize.py` | 统一字段、去重、生成数据摘要 | 日期不完整时标注置信度，不把推断写成事实 |

## 安装

**方式一：一条命令安装（推荐）**

```bash
# 装到用户级，所有项目都能用
npx -y skills@latest add Wanzi-one/wanzi-research -g -y

# 只装到当前项目
npx -y skills@latest add Wanzi-one/wanzi-research -y

# 先看看会装什么、不真装
npx -y skills@latest add Wanzi-one/wanzi-research -l
```

因为本仓库根目录就是技能本身（`SKILL.md` 在根），所以**不需要**额外指定 `--skill` 参数。

装到哪里（实测）：CLI 会把技能放到 `~/.agents/skills/wanzi-research/`，再自动软链到它识别到的各个客户端（Claude Code、Codex、Cursor、Gemini CLI、Lingma、Trae 等 55+ 个），通常会看到 `Installed 1 skill`，末尾的 `Eve / PromptScript → does not support global skill installation` 属正常提示，不影响使用。

**WorkBuddy 用户注意**：该 CLI 尚未把 WorkBuddy 列入自动目标，装完请补一步，把技能放进 WorkBuddy 的技能目录：

```bash
# 复制（或改用 ln -s 建软链，便于跟随仓库更新）
cp -r ~/.agents/skills/wanzi-research ~/.workbuddy/skills/wanzi-research
```

**方式二：手动放入技能目录**（也适合离线分发压缩包）

把本目录复制到：

```text
~/.workbuddy/skills/wanzi-research/
```

目录结构：

```text
wanzi-research/
├── SKILL.md                      # 触发条件与执行纪律
├── README.md                     # 你正在读的这份
├── LICENSE                       # 自定义许可：免费使用·保留署名·禁止转卖
├── CHANGELOG.md                  # 更新记录
├── VERSION                       # 当前版本
├── config.example.json           # 非敏感配置模板
├── scripts/                      # 四个可独立运行的脚本
├── references/                   # 用到才读的知识文档
│   ├── configuration.md          # 密钥配置（含 macOS 坑位）
│   ├── api-endpoints.md          # 端点参数与实测坑位
│   ├── output-schema.md          # 字段标准与日期置信度
│   └── analysis-framework.md     # 四维拆解方法论
├── assets/report-template.html   # 奶油黄报告模板
├── agents/openai.yaml            # 技能展示元数据
└── evals/evals.json              # 验收用例
```

## 配置

本技能**不含任何 Key**。采集走你自己注册的付费数据网关（默认 TikHub）。

```bash
export TIKHUB_API_KEY="你的Key"
```

> macOS 用户注意：写进 `~/.zshrc` 在非交互式 shell 里读不到，写进 `~/.zshenv` 才稳。
> 详见 `references/configuration.md`。

检查是否就绪（不会显示 Key）：

```bash
python3 scripts/api_request.py --check-config
```

## 快速开始

```bash
# 1. 采集某账号全量作品（自动翻页、去重、可续跑）
python3 scripts/collect_notes.py \
  --platform xiaohongshu --user-id '<user_id>' \
  --tag myaccount --out-dir social-research/raw \
  --ledger social-research/ledger.json

# 2. 归一化 + 生成数据摘要
python3 scripts/normalize.py \
  --input social-research/raw/myaccount_merged.json \
  --out-csv social-research/normalized/myaccount_notes.csv \
  --stats social-research/normalized/myaccount_stats.md

# 3. 关键词找低粉高数据选题
python3 scripts/search_pick.py \
  --platform xiaohongshu --keyword '早睡' --pages 2 \
  --tag zaoshui --enrich-author 10 \
  --out-csv social-research/normalized/zaoshui.csv

# 4. 只做费用预览，不发请求
python3 scripts/api_request.py \
  --path '/api/v1/xiaohongshu/app_v2/get_user_posted_notes' \
  --estimate-requests 106 --unit-price 0.001 --dry-run
```

在对话里直接说话也行：**「用碗子调研拆解这个博主」**、**「搜今天讲早睡的内容，找低粉高数据的选题」**。

## 四条执行纪律

技能会在任何操作里守住这四条，也是它最核心的价值：

1. **先把钱说清楚**——批量请求前给出请求数与费用预览，只写预估不承诺金额；
2. **先小样本再批量**——1–3 条验证字段，不通过就停下修端点；
3. **密钥永不进聊天**——只从本机环境变量或钥匙串读取；
4. **推断必须标注**——结论回指原始文件，客单价一类推断标「待验证」，品牌自述话术不当客观事实。

## 成本

技能代码免费使用（保留署名·禁止转卖，详见 `LICENSE`）。数据采集费用由你直接付给数据网关，与作者无关。

粗略量级：多数端点约 `0.001–0.01 USD / 次`。一次账号拆解通常 10–30 次请求；关键词选题约 10–15 次。**精确价格以网关官方价格页与价格计算接口为准**，脚本支持 `--official-price` 实时核准。

## 数据与合规

- 只采集公开数据，不绕过登录、验证码或付费墙；
- 不采集密码、Cookie、会话令牌或支付信息；
- 交付物不包含可复用凭据；
- 请遵守目标平台服务条款与所在地法律法规。

## 许可

自定义许可（`LICENSE`）：**免费使用 · 保留署名 · 禁止转卖**。

- ✅ 可以：免费用（含商业机构内部使用）、修改、创作衍生作品、分发
- ⚠️ 必须：保留 `LICENSE` 与版权署名
- ❌ 不可：出售、出租、按订阅收费，或整体打包进付费课程／付费社群／付费工具包
- 📩 需要对外提供付费服务或商业嵌入，请联系作者取得单独授权

> 这不是 OSI 认可的标准协议，而是「源码可见」的自定义许可。若你的组织只接受标准开源协议，请先与作者确认。
