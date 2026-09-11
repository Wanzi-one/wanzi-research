# 配置与密钥

本技能**不包含任何真实 API Key**。第一次做付费采集前读完本文件。

## 费用关系

- 本技能（碗子调研）代码免费使用，许可条款见项目根目录 `LICENSE`（保留署名·禁止转卖）；
- 自动采集走用户自己注册的付费数据网关（默认 TikHub），费用由用户直接向网关支付；
- 本技能与任何数据网关服务商之间**没有隶属、代理、转售、代充或商务背书关系**，网关的价格、稳定性与售后由网关方负责；
- 本技能承诺不了任何固定单价、永久免费、无限额度或服务可用性；
- 每次批量任务前，请到网关官方价格页与账户余额处确认当前口径。

默认网关入口（以官方当前页面为准）：

- 价格：https://tikhub.io/pricing
- 新手接入：https://tikhub.io/getting-started
- API 文档：https://docs.tikhub.io/
- API Explorer / OpenAPI：https://api.tikhub.io/

## 配置方式

### 方式一：环境变量（推荐，跨平台通用）

```bash
export TIKHUB_API_KEY="替换成你自己的 Key"
```

**macOS 上的关键坑位（实测踩过）**：把变量写进 `~/.zshrc` 后，非交互式 shell（脚本、自动化调用、部分编辑器内置终端）**不会加载**它，表现为「明明配了却读不到 Key」。

正确的做法是写进 `~/.zshenv`（zsh 对每次启动都会读取）：

```bash
echo 'export TIKHUB_API_KEY="你的Key"' >> ~/.zshenv
chmod 600 ~/.zshenv
```

bash 用户可写 `~/.bash_profile`，但同样注意非交互 shell 的行为差异。另一条更稳的路是方式二。

### 方式二：macOS 钥匙串

用「钥匙串访问」新建「通用密码」：

- 服务名称：`tikhub-api`
- 账户名称：`tikhub`
- 密码：你自己的 Key

脚本会先读环境变量，读不到再读钥匙串。

### 方式三：仅当前命令临时生效

```bash
TIKHUB_API_KEY="你的Key" python3 scripts/api_request.py --check-config
```

## 检查配置

```bash
python3 scripts/api_request.py --check-config
```

只输出是否已配置、来源、变量名和 API Base，**不会打印 Key**。

想跳过钥匙串读取（用于测试）：

```bash
TIKHUB_DISABLE_KEYCHAIN=1 python3 scripts/api_request.py --check-config
```

## API Base 覆盖

默认：

```text
https://api.tikhub.io
```

若网关对你所在地区提供了不同入口：

```bash
export TIKHUB_API_BASE="https://官方当前推荐的地址"
```

也可以用配置文件覆盖（复制 `config.example.json` 到个人目录，用 `--config` 指定）。**配置文件只放非敏感项。**

## 读取 Key 的两个真实坑位

1. **换行符导致 401**：用 `grep`/`cut` 从 shell 配置文件里提取 Key 时容易带上换行符，请求会返回 401（看起来像 Key 失效）。稳妥做法是用 Python 正则提取并 `strip()`：

   ```python
   import re
   key = re.search(r'TIKHUB_API_KEY="([^"]+)"', open('/Users/你/.zshenv').read()).group(1).strip()
   ```

2. **沙箱环境拦截钥匙串**：部分受限执行环境会直接杀掉 `security` 命令，此时钥匙串方案不可用，改用环境变量。

## 安全红线

- 不要把 Key 粘贴到对话、文档、截图、报告或 Git 仓库；
- 不要把 Key 写进技能目录里的任何文件；
- 共享 `raw/` 原始数据前先脱敏（原始响应可能带临时媒体链接与凭据参数）；
- 报告与日志中不出现 `Authorization`、`token=`、`sign=`、`cache_url`、`decode_key` 等可复用凭据。
