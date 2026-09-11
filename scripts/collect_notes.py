#!/usr/bin/env python3
"""碗子调研 · 账号作品全量采集器。

把手写翻页脚本固化成可复用工具，专门解决四个真实踩过的坑：

1. 漏页：翻页条件写错导致少拉几页（曾漏掉一条 16 万赞爆款，直接影响结论）；
2. 重复：置顶内容会在每一页重复出现，按 id 去重才不会虚增条数；
3. 中断：长批量被系统杀掉后只能从头再来，这里支持 --resume 断点续跑；
4. 不可追溯：中间文件被反复覆盖，这里原始页永不覆盖、合并结果单独落盘。

原始响应逐页落盘（raw），合并结果另存（merged），两者都不加工。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REQUEST_SCRIPT = SCRIPT_DIR / "api_request.py"

# 平台适配表：新增平台时按同样结构补一条即可
PLATFORMS: dict[str, dict[str, Any]] = {
    "xiaohongshu": {
        "endpoint": "/api/v1/xiaohongshu/app_v2/get_user_posted_notes",
        "id_param": "user_id",
        "list_path": ["data", "data", "notes"],
        "has_more_path": ["data", "data", "has_more"],
        "cursor_mode": "item",          # 游标来自本页最后一条的字段
        "cursor_field": "cursor",
        "page_param": "cursor",
        "item_id_field": "id",
        "productish_field": "is_goods_note",
    },
    "douyin": {
        "endpoint": "/api/v1/douyin/web/fetch_user_post_videos",
        "id_param": "sec_user_id",
        "list_path": ["data", "data", "aweme_list"],
        "has_more_path": ["data", "data", "has_more"],
        "cursor_mode": "response",      # 游标来自响应体
        "cursor_path": ["data", "data", "max_cursor"],
        "page_param": "max_cursor",
        "page_size_param": "count",
        "page_size": 20,
        "item_id_field": "aweme_id",
        "productish_field": None,
    },
}


def dig(payload: Any, path: list[str]) -> Any:
    for key in path:
        if isinstance(payload, dict) and key in payload:
            payload = payload[key]
        else:
            return None
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORMS), help="平台")
    parser.add_argument("--user-id", required=True, help="账号 ID（小红书 user_id / 抖音 sec_user_id）")
    parser.add_argument("--out-dir", default="social-research/raw", help="原始响应目录")
    parser.add_argument("--tag", required=True, help="本次任务的短标识，用于文件命名，如 ouyang")
    parser.add_argument("--max-pages", type=int, default=40, help="安全上限，默认 40 页")
    parser.add_argument("--sleep", type=float, default=1.2, help="页间间隔秒数，默认 1.2")
    parser.add_argument("--resume", action="store_true", help="跳过已存在的页文件，用于中断后续跑")
    parser.add_argument("--ledger", help="请求账本路径，透传给请求内核")
    parser.add_argument("--page-size", type=int, help="每页条数，仅对支持该参数的平台生效")
    return parser.parse_args()


def fetch_page(
    *,
    adapter: dict[str, Any],
    user_id: str,
    cursor: Any,
    page_no: int,
    out_dir: Path,
    tag: str,
    ledger: str | None,
    resume: bool,
    page_size: int | None,
) -> tuple[dict[str, Any] | None, Path, bool]:
    """返回 (响应体, 落盘路径, 是否真的发起了网络请求)。"""
    out_path = out_dir / f"{tag}_p{page_no}.json"
    if resume and out_path.exists():
        try:
            return json.loads(out_path.read_text(encoding="utf-8")), out_path, False
        except json.JSONDecodeError:
            pass  # 文件损坏则重新请求

    params: dict[str, Any] = {adapter["id_param"]: user_id}
    if cursor not in (None, "", 0):
        params[adapter["page_param"]] = cursor
    if page_size and adapter.get("page_size_param"):
        params[adapter["page_size_param"]] = page_size

    cmd = [
        sys.executable,
        str(REQUEST_SCRIPT),
        "--method",
        "GET",
        "--path",
        adapter["endpoint"],
        "--params",
        json.dumps(params, ensure_ascii=False),
        "--out",
        str(out_path),
        "--quiet",
    ]
    if ledger:
        cmd += ["--ledger", ledger]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"第 {page_no} 页请求失败：{result.stderr.strip()[:400]}", file=sys.stderr)
        return None, out_path, True
    try:
        return json.loads(out_path.read_text(encoding="utf-8")), out_path, True
    except (OSError, json.JSONDecodeError) as exc:
        print(f"第 {page_no} 页响应无法解析：{exc}", file=sys.stderr)
        return None, out_path, True


def main() -> int:
    args = parse_args()
    adapter = PLATFORMS[args.platform]
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    cursor: Any = None
    page_no = 1
    seen: dict[str, dict[str, Any]] = {}
    pages_read = 0
    requests_made = 0
    failures = 0
    last_page_seen: list[str] = []

    while page_no <= args.max_pages:
        page, out_path, did_request = fetch_page(
            adapter=adapter,
            user_id=args.user_id,
            cursor=cursor,
            page_no=page_no,
            out_dir=out_dir,
            tag=args.tag,
            ledger=args.ledger,
            resume=args.resume,
            page_size=args.page_size,
        )
        if page is None:
            failures += 1
            if failures >= 2:
                print("连续失败两次，已停止。已完成的分页文件保留，可用 --resume 继续。", file=sys.stderr)
                break
            page_no += 1
            continue
        failures = 0
        if did_request:
            requests_made += 1

        items = dig(page, adapter["list_path"]) or []
        if not isinstance(items, list):
            items = []

        new_ids = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get(adapter["item_id_field"]) or "")
            if not item_id:
                continue
            if item_id not in seen:
                seen[item_id] = item
                new_ids.append(item_id)

        last_page_seen = [str(i.get(adapter["item_id_field"]) or "") for i in items if isinstance(i, dict)]
        pages_read += 1
        print(
            f"第 {page_no} 页{'（读取本地文件）' if not did_request else ''}：本页 {len(items)} 条，"
            f"新增 {len(new_ids)} 条，累计唯一 {len(seen)} 条",
            flush=True,
        )

        has_more = dig(page, adapter["has_more_path"])
        if not items or has_more is False:
            print(f"翻页结束：has_more={has_more}，本页条数={len(items)}")
            break

        if adapter["cursor_mode"] == "item":
            cursor = items[-1].get(adapter["cursor_field"]) if isinstance(items[-1], dict) else None
            if not cursor:
                print("本页未取到游标，停止翻页（请核对端点分页字段）。", file=sys.stderr)
                break
        else:
            cursor = dig(page, adapter["cursor_path"])
            if cursor in (None, 0, "0", ""):
                print("响应未取到下一页游标，停止翻页。", file=sys.stderr)
                break

        page_no += 1
        time.sleep(args.sleep)
    else:
        print(f"达到页数上限 {args.max_pages}，可能仍有更早内容未采集。", file=sys.stderr)

    merged = list(seen.values())
    merged_path = out_dir / f"{args.tag}_merged.json"
    merged_path.write_text(
        json.dumps(
            {
                "platform": args.platform,
                "user_id": args.user_id,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "pages_read": pages_read,
                "unique_items": len(merged),
                "items": merged,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "platform": args.platform,
                "pages_read": pages_read,
                "requests_made": requests_made,
                "unique_items": len(merged),
                "merged": str(merged_path.resolve()),
                "note": "原始分页文件保留在 out-dir，未覆盖；merged 为按 id 去重后的结果。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if merged else 1


if __name__ == "__main__":
    raise SystemExit(main())
