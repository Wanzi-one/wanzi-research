#!/usr/bin/env python3
"""碗子调研 · 数据网关请求内核。

职责只有四件事：
1. 从本机安全读取 Key（环境变量 / macOS 钥匙串），永不打印 Key；
2. 请求前给出可核对的价格预估与脱敏后的请求预览；
3. 执行请求并把原始响应落盘（不加工、不覆盖）；
4. 把每次真实请求记进账本，方便事后核对请求数与费用。

用法示例见 references/api-endpoints.md。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = {
    "api_base": "https://api.tikhub.io",
    "timeout_seconds": 45,
    "api_key_env": "TIKHUB_API_KEY",
    "macos_keychain_service": "tikhub-api",
    "macos_keychain_account": "tikhub",
    "user_agent": "Wanzi-Research/1.0",
}

# 官方公开的阶梯折扣口径，仅用于离线量级预估，不作为报价
PRICE_TIERS = (
    (1_000, Decimal("0")),
    (5_000, Decimal("0.10")),
    (10_000, Decimal("0.20")),
    (20_000, Decimal("0.30")),
    (30_000, Decimal("0.40")),
    (None, Decimal("0.50")),
)

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "key",
    "api_key",
    "apikey",
    "secret",
    "sign",
    "cache_url",
}

RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config(path: str | None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if path:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("--config 必须是一个 JSON 对象")
        config.update(payload)
    config["api_base"] = os.environ.get("TIKHUB_API_BASE", str(config["api_base"])).rstrip("/")
    return config


def read_keychain(service: str, account: str) -> str:
    if platform.system() != "Darwin":
        return ""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def resolve_api_key(config: dict[str, Any]) -> tuple[str, str]:
    env_name = str(config["api_key_env"])
    value = os.environ.get(env_name, "").strip()
    if value:
        return value, f"environment:{env_name}"
    if os.environ.get("TIKHUB_DISABLE_KEYCHAIN") == "1":
        return "", "missing"
    value = read_keychain(
        str(config["macos_keychain_service"]),
        str(config["macos_keychain_account"]),
    )
    if value:
        return value, "macOS Keychain"
    return "", "missing"


def parse_json_arg(raw: str | None) -> Any:
    if not raw:
        return None
    if raw.startswith("@"):
        return json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
    return json.loads(raw)


def validate_path(path: str) -> None:
    if not path.startswith("/api/") or path.startswith("//"):
        raise ValueError("--path 必须以且仅以一个 '/api/' 开头")


def build_url(base_url: str, path: str, params: Any) -> str:
    validate_path(path)
    if params is not None and not isinstance(params, dict):
        raise ValueError("--params 必须是 JSON 对象")
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return url


def redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_pairs = [(k, "***" if k.lower() in SENSITIVE_KEYS else v) for k, v in pairs]
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(safe_pairs), "")
    )


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if k.lower() in SENSITIVE_KEYS else redact_payload(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def estimate_cost(requests: int, unit_price: Decimal) -> dict[str, Any]:
    if requests < 1:
        raise ValueError("--estimate-requests 必须是正整数")
    if unit_price <= 0:
        raise ValueError("--unit-price 必须大于 0")

    remaining = requests
    lower = 0
    total = Decimal("0")
    breakdown = []
    for upper, discount in PRICE_TIERS:
        if remaining <= 0:
            break
        capacity = remaining if upper is None else max(0, upper - lower)
        count = min(remaining, capacity)
        discounted = unit_price * (Decimal("1") - discount)
        tier_cost = discounted * count
        breakdown.append(
            {
                "requests": count,
                "discount_percent": float(discount * 100),
                "unit_price_usd": float(discounted),
                "cost_usd": float(tier_cost),
            }
        )
        total += tier_cost
        remaining -= count
        if upper is not None:
            lower = upper

    return {
        "requests": requests,
        "base_unit_price_usd": float(unit_price),
        "estimated_total_usd": float(total),
        "average_unit_price_usd": float(total / requests),
        "tiers": breakdown,
        "disclaimer": "仅为量级预估，付费批量前必须用具体端点单价或官方价格计算接口复核。",
    }


def price_preview(requests: int, unit_price_raw: str | None) -> dict[str, Any]:
    if unit_price_raw:
        try:
            unit_price = Decimal(unit_price_raw)
        except InvalidOperation as exc:
            raise ValueError("--unit-price 必须是合法十进制数") from exc
        return {"exact_input": estimate_cost(requests, unit_price)}
    return {
        "typical_range": {
            "low": estimate_cost(requests, Decimal("0.001")),
            "high": estimate_cost(requests, Decimal("0.01")),
        },
        "warning": "少数特殊端点单价可能高于 0.01 USD/次，务必核对所选端点。",
    }


def append_ledger(path: str | None, record: dict[str, Any]) -> None:
    if not path:
        return
    ledger_path = Path(path).expanduser()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if ledger_path.exists():
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = []
    else:
        payload = []
    if not isinstance(payload, list):
        payload = []
    payload.append(record)
    ledger_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def request_json(
    *,
    config: dict[str, Any],
    api_key: str,
    method: str,
    path: str,
    params: Any = None,
    body: Any = None,
    timeout: int | None = None,
    retries: int = 2,
) -> tuple[Any, int | None]:
    """执行一次请求。返回 (响应体, HTTP状态码)。仅对限速与服务端错误重试。"""
    url = build_url(str(config["api_base"]), path, params)
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": str(config["user_agent"]),
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request_timeout = int(timeout or config["timeout_seconds"])
    attempt = 0
    while True:
        attempt += 1
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw = response.read()
            try:
                return json.loads(raw), 200
            except json.JSONDecodeError:
                return {"raw_text": raw.decode("utf-8", errors="replace")}, 200
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").replace(api_key, "<redacted>")
            hint = {
                401: "Key 无效、过期或请求头格式不对（Key 需带 Bearer 前缀，由本脚本自动处理）。",
                402: "余额或额度不足，请到 TikHub 账户充值或换用更低成本端点。",
                403: "无权限访问该端点（可能需要更高套餐）。",
                404: "端点路径不存在，请以 OpenAPI 为准核对 --path。",
                422: "参数不符合端点要求，请核对参数名与取值。",
                429: "触发频率限制，请降低并发、缩小范围或稍后重试。",
            }.get(exc.code, "")
            message = f"网关返回 HTTP {exc.code}：{detail[:600]}"
            if hint:
                message += f"\n处理建议：{hint}"
            if exc.code in RETRYABLE_HTTP and attempt <= retries:
                wait = min(2 ** attempt, 8)
                print(f"{message}\n将在 {wait}s 后重试（第 {attempt}/{retries} 次）…", file=sys.stderr)
                time.sleep(wait)
                continue
            print(message, file=sys.stderr)
            raise SystemExit(1) from exc
        except urllib.error.URLError as exc:
            if attempt <= retries:
                wait = min(2 ** attempt, 8)
                print(
                    f"网络请求失败：{exc.reason}，将在 {wait}s 后重试（第 {attempt}/{retries} 次）…",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            print(
                f"网络请求失败：{exc.reason}\n"
                "排查建议：确认网络可达；若本机开启了代理且代理不可用，可尝试绕过代理直连。",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="可选的非敏感 JSON 配置文件")
    parser.add_argument("--check-config", action="store_true", help="只检查 Key 是否就绪，不显示 Key")
    parser.add_argument("--method", choices=("GET", "POST"), default="GET")
    parser.add_argument("--path", help="数据端点路径，必须以 /api/ 开头")
    parser.add_argument("--params", help="查询参数 JSON 对象，或 @文件名")
    parser.add_argument("--body", help="POST 请求体 JSON，或 @文件名")
    parser.add_argument("--out", help="真实请求的原始响应落盘路径")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--retries", type=int, default=2, help="限速/服务端错误的重试次数，默认 2")
    parser.add_argument("--dry-run", action="store_true", help="只打印脱敏后的请求预览，不发请求")
    parser.add_argument("--estimate-requests", type=int, help="计划成功请求数，用于价格预估")
    parser.add_argument("--unit-price", help="已知端点单价 USD/次；不填则按 0.001–0.01 给区间")
    parser.add_argument("--official-price", action="store_true", help="调用官方价格计算接口")
    parser.add_argument("--ledger", help="请求账本 JSON 路径；每次真实请求追加一条记录")
    parser.add_argument("--quiet", action="store_true", help="成功时不打印摘要")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"配置无效：{exc}", file=sys.stderr)
        return 2

    api_key, source = resolve_api_key(config)
    if args.check_config:
        print(
            json.dumps(
                {
                    "configured": bool(api_key),
                    "source": source,
                    "variable": config["api_key_env"],
                    "api_base": config["api_base"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not args.path:
        print("除 --check-config 外，必须提供 --path", file=sys.stderr)
        return 2

    try:
        validate_path(args.path)
        params = parse_json_arg(args.params)
        body = parse_json_arg(args.body)
        preview = (
            price_preview(args.estimate_requests, args.unit_price)
            if args.estimate_requests is not None
            else None
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"请求参数无效：{exc}", file=sys.stderr)
        return 2

    if args.official_price:
        if args.estimate_requests is None:
            print("--official-price 需要同时提供 --estimate-requests", file=sys.stderr)
            return 2
        if not api_key:
            print(
                "未检测到 API Key。可先用 --dry-run 看区间预估；"
                "请在本地配置 Key，不要粘贴到聊天里。",
                file=sys.stderr,
            )
            return 2
        payload, _ = request_json(
            config=config,
            api_key=api_key,
            method="GET",
            path="/api/v1/tikhub/user/calculate_price",
            params={"endpoint": args.path, "request_per_day": args.estimate_requests},
            timeout=args.timeout,
            retries=args.retries,
        )
        print(
            json.dumps(
                {
                    "endpoint": args.path,
                    "request_per_day": args.estimate_requests,
                    "source": "官方价格计算接口",
                    "result": redact_payload(payload),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        url = build_url(str(config["api_base"]), args.path, params)
    except ValueError as exc:
        print(f"请求参数无效：{exc}", file=sys.stderr)
        return 2

    request_preview = {
        "method": args.method,
        "url": redact_url(url),
        "body": redact_payload(body),
        "out": args.out,
        "authorization": "Bearer <从本机环境变量或钥匙串读取>",
        "pricing": preview,
    }
    if args.dry_run:
        print(json.dumps(request_preview, ensure_ascii=False, indent=2))
        return 0

    if not api_key:
        print(
            "未检测到 API Key，已停止付费采集。\n"
            "请先在本地配置（见 references/configuration.md），或用 --dry-run 只做预览。\n"
            "注意：不要为了跑通而改用其他未经确认的数据来源。",
            file=sys.stderr,
        )
        return 2
    if not args.out:
        print("真实请求必须提供 --out 保存原始响应", file=sys.stderr)
        return 2

    payload, _ = request_json(
        config=config,
        api_key=api_key,
        method=args.method,
        path=args.path,
        params=params,
        body=body,
        timeout=args.timeout,
        retries=args.retries,
    )
    output_path = Path(args.out).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    code = payload.get("code") if isinstance(payload, dict) else None
    message = payload.get("message") if isinstance(payload, dict) else ""
    ok = code == 200 if code is not None else True

    append_ledger(
        args.ledger,
        {
            "ts": now_iso(),
            "endpoint": args.path,
            "method": args.method,
            "params": redact_payload(params),
            "ok": bool(ok),
            "code": code,
            "message": message,
            "out": str(output_path.resolve()),
        },
    )

    if not args.quiet:
        print(
            json.dumps(
                {
                    "saved": str(output_path.resolve()),
                    "code": code,
                    "message": message,
                    "ok": bool(ok),
                    "pricing_preview": preview,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
