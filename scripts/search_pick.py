#!/usr/bin/env python3
"""碗子调研 · 关键词搜索与低粉高数据筛选。

把"搜一批内容 → 挑出值得讲的选题"固定成流程：

1. 按关键词搜索并翻页，原始响应全部落盘；
2. 自动从响应中定位内容列表（兼容不同端点的嵌套层级）；
3. 可选补齐作者粉丝数（同一作者只查一次，控制成本）；
4. 按效率分排序，输出候选表。

效率分 = (赞 + 评论×3 + 收藏×2 + 转发×5) / max(粉丝数, 1000)
   · 加权理由是评论/收藏/转发的获取难度与意图强度高于点赞，属经验权重；
   · 这不是任何平台的官方指标，只用于本次筛选内部排序，不得写进报告当结论；
   · 未补齐粉丝数时，脚本只按加权互动排序，并在输出中标注"粉丝数待补"。
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REQUEST_SCRIPT = SCRIPT_DIR / "api_request.py"

SEARCH_PLATFORMS: dict[str, dict[str, Any]] = {
    "douyin": {
        "endpoint": "/api/v1/douyin/search/fetch_general_search_v3",
        "keyword_param": "keyword",
        "page_param": "cursor",
        "page_size_param": "count",
        "page_size": 20,
        "list_paths": [
            ["data", "data", "data"],
            ["data", "data", "business_data"],
            ["data", "data", "aweme_list"],
        ],
        "detail_endpoint": "/api/v1/douyin/web/fetch_one_video",
        "detail_id_param": "aweme_id",
    },
    "xiaohongshu": {
        "endpoint": "/api/v1/xiaohongshu/app_v2/search_notes",
        "keyword_param": "keyword",
        "page_param": "page",
        "page_size_param": None,
        "list_paths": [
            ["data", "data", "items"],
            ["data", "data", "notes"],
            ["data", "data", "data"],
        ],
        "detail_endpoint": "/api/v1/xiaohongshu/app_v2/get_user_info",
        "detail_id_param": "user_id",
    },
}

# 抖音搜索结果是 {type, aweme_info:{...}}，需要拆壳
WRAPPER_KEYS = ("aweme_info", "note_card", "note", "aweme")


def dig(payload: Any, path: list[str]) -> Any:
    for key in path:
        if isinstance(payload, dict) and key in payload:
            payload = payload[key]
        else:
            return None
    return payload


def find_list(payload: Any, paths: list[list[str]], depth: int = 0) -> list[Any]:
    for path in paths:
        found = dig(payload, path)
        if isinstance(found, list) and found:
            return found
    if depth > 6:
        return []
    candidates: list[list[Any]] = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                candidates.append(value)
            elif isinstance(value, dict):
                found = find_list(value, paths, depth + 1)
                if found:
                    return found
    if candidates:
        return max(candidates, key=len)
    return []


def unwrap(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    for key in WRAPPER_KEYS:
        inner = item.get(key)
        if isinstance(inner, dict):
            return inner
    return item


def title_of(item: dict[str, Any]) -> str:
    for key in ("display_title", "title", "desc"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def author_of(item: dict[str, Any]) -> tuple[str, str]:
    author = item.get("author") or item.get("user") or {}
    if not isinstance(author, dict):
        return "", ""
    name = author.get("nickname") or author.get("nick_name") or author.get("name") or ""
    # 字段名因平台而异：小红书搜索结果用 userid，抖音用 sec_uid，详情接口用 user_id
    identifier = ""
    for key in ("sec_uid", "sec_user_id", "user_id", "userid", "uid", "id"):
        value = author.get(key)
        if value:
            identifier = value
            break
    return str(name), str(identifier)


def metrics_of(item: dict[str, Any]) -> dict[str, float]:
    stats = item.get("statistics") or item.get("interact_info") or {}
    if not isinstance(stats, dict):
        stats = {}

    def grab(*keys: str) -> float:
        for key in keys:
            value = stats.get(key)
            if value is None:
                value = item.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                text = value.strip()
                if text.isdigit():
                    return float(text)
                if text and text[-1] in "wW万":
                    try:
                        return float(text[:-1]) * 10000
                    except ValueError:
                        pass
                if text and text[-1] in "kK":
                    try:
                        return float(text[:-1]) * 1000
                    except ValueError:
                        pass
        return 0.0

    return {
        "likes": grab("digg_count", "liked_count", "likes", "likes_count"),
        "comments": grab("comment_count", "comments_count", "comments"),
        "favorites": grab("collect_count", "collected_count", "favorites"),
        "shares": grab("share_count", "shared_count", "shares"),
    }


def content_id_of(item: dict[str, Any]) -> str:
    for key in ("aweme_id", "id", "note_id"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def weighted_engagement(metrics: dict[str, float]) -> float:
    return (
        metrics["likes"]
        + metrics["comments"] * 3
        + metrics["favorites"] * 2
        + metrics["shares"] * 5
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", required=True, choices=sorted(SEARCH_PLATFORMS))
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--pages", type=int, default=2, help="搜索翻页数，默认 2")
    parser.add_argument("--out-dir", default="social-research/raw")
    parser.add_argument("--tag", required=True, help="文件短标识，如 jianfei")
    parser.add_argument("--extra-params", help="补充端点参数 JSON，如排序与时间筛选")
    parser.add_argument("--enrich-author", type=int, default=0,
                        help="对加权互动最高的 N 条补齐作者粉丝数，会额外产生请求")
    parser.add_argument("--top", type=int, default=20, help="输出候选条数")
    parser.add_argument("--sleep", type=float, default=1.2)
    parser.add_argument("--ledger", help="请求账本路径")
    parser.add_argument("--out-csv")
    return parser.parse_args()


def call_api(*, path: str, params: dict[str, Any], out_path: Path, ledger: str | None) -> dict[str, Any] | None:
    cmd = [
        sys.executable, str(REQUEST_SCRIPT),
        "--method", "GET",
        "--path", path,
        "--params", json.dumps(params, ensure_ascii=False),
        "--out", str(out_path),
        "--quiet",
    ]
    if ledger:
        cmd += ["--ledger", ledger]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"请求失败：{path}\n{result.stderr.strip()[:400]}", file=sys.stderr)
        return None
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    args = parse_args()
    adapter = SEARCH_PLATFORMS[args.platform]
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    extra = json.loads(args.extra_params) if args.extra_params else {}
    collected: dict[str, dict[str, Any]] = {}
    requests_made = 0

    for page in range(1, args.pages + 1):
        params: dict[str, Any] = {adapter["keyword_param"]: args.keyword}
        if page > 1:
            params[adapter["page_param"]] = page if adapter["page_param"] == "page" else ""
        if adapter["page_size_param"] and adapter["page_size"]:
            params[adapter["page_size_param"]] = adapter["page_size"]
        params.update(extra)

        out_path = out_dir / f"{args.tag}_search_p{page}.json"
        payload = call_api(path=adapter["endpoint"], params=params, out_path=out_path, ledger=args.ledger)
        requests_made += 1
        if payload is None:
            print(f"第 {page} 页搜索失败，停止翻页。", file=sys.stderr)
            break

        raw_items = find_list(payload, adapter["list_paths"])
        print(f"第 {page} 页：识别到 {len(raw_items)} 个条目", flush=True)
        if not raw_items:
            print(f"未识别到内容列表，原始响应已保存到 {out_path}，请据此核对端点结构。", file=sys.stderr)
            break

        for raw in raw_items:
            item = unwrap(raw)
            content_id = content_id_of(item)
            if not content_id or content_id in collected:
                continue
            name, identifier = author_of(item)
            metrics = metrics_of(item)
            collected[content_id] = {
                "content_id": content_id,
                "title": title_of(item),
                "author_name": name,
                "author_id": identifier,
                "likes": metrics["likes"],
                "comments": metrics["comments"],
                "favorites": metrics["favorites"],
                "shares": metrics["shares"],
                "weighted": weighted_engagement(metrics),
                "followers": "",
            }
        if page < args.pages:
            time.sleep(args.sleep)

    if not collected:
        print("没有采集到任何内容。", file=sys.stderr)
        return 1

    rows = list(collected.values())

    # 补齐作者粉丝数：只查加权互动最高的 N 条，同一作者去重
    if args.enrich_author > 0:
        targets = sorted(rows, key=lambda r: r["weighted"], reverse=True)[: args.enrich_author]
        cache: dict[str, str] = {}
        enriched = 0
        for row in targets:
            identifier = row["author_id"]
            if not identifier:
                continue
            if identifier in cache:
                row["followers"] = cache[identifier]
                continue
            out_path = out_dir / f"{args.tag}_author_{identifier[-12:]}.json"
            payload = call_api(
                path=adapter["detail_endpoint"],
                params={adapter["detail_id_param"]: identifier if args.platform == "xiaohongshu" else row["content_id"]},
                out_path=out_path,
                ledger=args.ledger,
            )
            requests_made += 1
            if payload is None:
                continue
            data = payload.get("data", {})
            inner = data.get("data", data) if isinstance(data, dict) else {}
            followers = ""
            if isinstance(inner, dict):
                if args.platform == "douyin":
                    author = dig(inner, ["aweme_detail", "author"]) or {}
                    if isinstance(author, dict):
                        followers = author.get("follower_count") or ""
                else:
                    followers = inner.get("fans") or inner.get("follower_count") or ""
            if followers != "":
                cache[identifier] = str(followers)
                row["followers"] = str(followers)
                enriched += 1
            time.sleep(args.sleep)
        print(f"已补齐 {enriched} 位作者的粉丝数（去重后请求 {len(cache)} 次）")

    # 排序：有粉丝数时按效率分，否则按加权互动
    with_followers = [r for r in rows if str(r.get("followers", "")).strip().isdigit()]
    for row in rows:
        followers_text = str(row.get("followers", "")).strip()
        if followers_text.isdigit() and int(followers_text) > 0:
            row["efficiency"] = round(row["weighted"] / max(int(followers_text), 1000), 4)
        else:
            row["efficiency"] = ""
    rows.sort(key=lambda r: (r["efficiency"] if r["efficiency"] != "" else 0, r["weighted"]), reverse=True)

    top_rows = rows[: args.top]
    print()
    print(f"== {args.keyword} · 候选 TOP {len(top_rows)}（共 {len(rows)} 条去重内容，请求 {requests_made} 次）==")
    header = f"{'#':>2} {'赞':>7} {'评':>6} {'藏':>6} {'粉丝':>8} {'效率分':>7}  标题"
    print(header)
    for index, row in enumerate(top_rows, start=1):
        fans = row["followers"] if str(row["followers"]).strip() else "待补"
        print(
            f"{index:>2} {row['likes']:>7.0f} {row['comments']:>6.0f} {row['favorites']:>6.0f} "
            f"{fans:>8} {row['efficiency'] if row['efficiency'] != '' else '-':>7}  {row['title'][:36]}"
        )
    if not with_followers:
        print("\n提示：未补齐粉丝数，当前按加权互动排序，尚不能判断'低粉高数据'。加 --enrich-author N 可补齐。")

    if args.out_csv:
        csv_path = Path(args.out_csv).expanduser()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "content_id", "title", "author_name", "author_id",
                    "likes", "comments", "favorites", "shares",
                    "weighted", "followers", "efficiency",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n候选表已写出：{csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
