#!/usr/bin/env python3
"""碗子调研 · 归一化与数据摘要。

把 collect_notes.py 产出的 merged.json 转成两类东西：
1. 统一字段的 CSV/JSON（字段口径见 references/output-schema.md），便于筛选和二次分析；
2. 一份数据摘要（Markdown），把"画像结论"需要的统计量一次算齐。

诚实性设计：日期解析会标注置信度。平台返回 '7-6' 这类不完整日期时，
不猜年份，而是标为 inferred_year 并在摘要中单独说明，避免把推断写成事实。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

MEDIA_LABEL = {"video": "视频", "normal": "图文", "image": "图文", "note": "图文"}


def parse_published(raw: Any) -> tuple[str, str]:
    """返回 (日期, 置信度)。置信度：exact / inferred_year / relative / unknown"""
    if raw is None or raw == "":
        return "", "unknown"
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        try:
            ts = float(raw)
            if ts > 1e12:  # 毫秒
                ts /= 1000
            if ts < 1e9:  # 明显不是本世纪的时间戳，判定为无效值
                return "", "unknown"
            return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), "exact"
        except (ValueError, OSError, OverflowError):
            return "", "unknown"
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt).strftime("%Y-%m-%d"), "exact"
        except ValueError:
            continue
    # MM-DD：年份无法确定
    if len(text) <= 5 and "-" in text:
        return text, "inferred_year"
    lower = text.lower()
    if "ago" in lower or "yesterday" in lower or "昨天" in text or "小时前" in text or "分钟前" in text:
        return "近期", "relative"
    return text, "unknown"


def pick(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def nested(item: dict[str, Any], *path: str) -> Any:
    current: Any = item
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def map_xiaohongshu(item: dict[str, Any]) -> dict[str, Any]:
    media = str(item.get("type") or "normal")
    return {
        "content_id": item.get("id") or item.get("note_id"),
        "title": (item.get("display_title") or item.get("title") or "").strip(),
        "caption": (item.get("desc") or "").strip(),
        "published_at_raw": item.get("time_desc") or item.get("create_time"),
        "published_ts": item.get("create_time"),
        "media_type": MEDIA_LABEL.get(media, media),
        "views": item.get("view_count"),
        "likes": item.get("likes"),
        "comments": item.get("comments_count"),
        "favorites": item.get("collected_count"),
        "shares": item.get("share_count"),
        "has_product": bool(item.get("is_goods_note")),
        "pinned": bool(item.get("sticky")),
        "note_type_raw": media,
    }


def map_douyin(item: dict[str, Any]) -> dict[str, Any]:
    stats = item.get("statistics") or {}
    aweme_type = item.get("aweme_type")
    media = "视频" if aweme_type in (0, 4, None) else f"type{aweme_type}"
    return {
        "content_id": item.get("aweme_id"),
        "title": (item.get("desc") or "").strip()[:120],
        "caption": (item.get("desc") or "").strip(),
        "published_at_raw": item.get("create_time"),
        "published_ts": item.get("create_time"),
        "media_type": media,
        "views": stats.get("play_count"),
        "likes": stats.get("digg_count"),
        "comments": stats.get("comment_count"),
        "favorites": stats.get("collect_count"),
        "shares": stats.get("share_count"),
        "has_product": bool(item.get("anchor_info") or item.get("promotions")),
        "pinned": bool(item.get("is_top")),
        "note_type_raw": str(aweme_type),
    }


MAPPERS = {"xiaohongshu": map_xiaohongshu, "douyin": map_douyin}


def num(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def stat_line(values: list[float]) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "无数据"
    return (
        f"中位 {statistics.median(values):.0f}｜均值 {statistics.mean(values):.0f}｜最大 {max(values):.0f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="collect_notes.py 产出的 merged.json")
    parser.add_argument("--platform", choices=sorted(MAPPERS))
    parser.add_argument("--out-csv", help="统一字段 CSV 输出路径")
    parser.add_argument("--out-json", help="统一字段 JSON 输出路径")
    parser.add_argument("--stats", help="数据摘要 Markdown 输出路径")
    parser.add_argument("--top", type=int, default=20, help="摘要中列出的高互动内容条数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.input).expanduser().read_text(encoding="utf-8"))
    platform = args.platform or payload.get("platform")
    if platform not in MAPPERS:
        print(f"无法确定平台，请用 --platform 指定（可选：{', '.join(MAPPERS)}）")
        return 2

    items = payload.get("items") or []
    if not items:
        print("merged.json 中没有 items")
        return 2

    mapper = MAPPERS[platform]
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = mapper(item)
        # 优先用平台时间戳（精确），失败再退化到文本日期
        date, confidence = parse_published(row.get("published_ts"))
        if confidence != "exact":
            text_date, text_conf = parse_published(row.get("published_at_raw"))
            if text_conf == "exact" or not date:
                date, confidence = text_date, text_conf
        row["published_at"] = date
        row["date_confidence"] = confidence
        row["platform"] = platform
        row["collected_at"] = payload.get("collected_at", "")
        row["source_file"] = str(Path(args.input).name)
        rows.append(row)

    # 去重（按内容 ID），保留首次出现
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("content_id") or "")
        if key and key not in deduped:
            deduped[key] = row
    rows = list(deduped.values())
    duplicates_removed = len(items) - len(rows)

    fieldnames = [
        "platform",
        "content_id",
        "title",
        "caption",
        "published_at",
        "date_confidence",
        "media_type",
        "views",
        "likes",
        "comments",
        "favorites",
        "shares",
        "has_product",
        "pinned",
        "collected_at",
        "source_file",
        "note_type_raw",
        "published_at_raw",
        "published_ts",
    ]

    if args.out_csv:
        csv_path = Path(args.out_csv).expanduser()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV 已写出：{csv_path}")

    if args.out_json:
        json_path = Path(args.out_json).expanduser()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"JSON 已写出：{json_path}")

    if not args.stats:
        return 0

    likes = [num(r.get("likes")) for r in rows]
    favorites = [num(r.get("favorites")) for r in rows]
    comments = [num(r.get("comments")) for r in rows]
    views = [num(r.get("views")) for r in rows]
    media_counter = Counter(str(r.get("media_type")) for r in rows)
    product_count = sum(1 for r in rows if r.get("has_product"))
    pinned = [r for r in rows if r.get("pinned")]
    confidence_counter = Counter(str(r.get("date_confidence")) for r in rows)

    exact_dates = sorted(
        r["published_at"] for r in rows if r.get("date_confidence") == "exact" and r.get("published_at")
    )
    year_counter = Counter(d[:4] for d in exact_dates)

    top_rows = sorted(rows, key=lambda r: num(r.get("likes")), reverse=True)[: args.top]
    median_likes = statistics.median(likes) if likes else 0
    favorite_ratio = (
        sum(favorites) / sum(likes) if sum(likes) > 0 else 0
    )

    lines: list[str] = []
    lines.append(f"# 数据摘要 · {platform}")
    lines.append("")
    lines.append(f"- 采集文件：`{Path(args.input).name}`")
    lines.append(f"- 采集时间：{payload.get('collected_at', '未记录')}")
    lines.append(f"- 分页读取：{payload.get('pages_read', '未记录')} 页")
    lines.append(f"- 有效内容：**{len(rows)}** 条（合并原始 {len(items)} 条，去重移除 {duplicates_removed} 条）")
    lines.append(f"- 内容形态：{'、'.join(f'{k} {v} 条' for k, v in media_counter.most_common())}")
    lines.append(
        f"- 转化/商品位：{product_count} 条（{product_count / len(rows) * 100:.0f}%）"
        if rows
        else "- 转化/商品位：0"
    )
    if pinned:
        lines.append(f"- 置顶内容：{len(pinned)} 条")
    lines.append("")
    lines.append("## 互动量级")
    lines.append("")
    lines.append(f"- 点赞：{stat_line(likes)}")
    lines.append(f"- 评论：{stat_line(comments)}")
    lines.append(f"- 收藏：{stat_line(favorites)}")
    if any(v > 0 for v in views):
        lines.append(f"- 播放/浏览：{stat_line(views)}")
    lines.append(f"- 收藏/赞 总量比：**{favorite_ratio:.2f}**（越高越偏'工具收藏型'，越低越偏'情绪转发型'）")
    lines.append(f"- 爆款参考线（中位赞 × 5）：**{median_likes * 5:.0f}**")
    lines.append("")
    lines.append("## 发布时间")
    lines.append("")
    if exact_dates:
        lines.append(f"- 可确证范围：{exact_dates[0]} → {exact_dates[-1]}")
    else:
        lines.append("- 可确证范围：不足以判断（平台未返回完整日期）")
    if year_counter:
        lines.append(
            "- 年度分布：" + "｜".join(f"{year} 年 {count} 条" for year, count in sorted(year_counter.items()))
        )
    note = {
        "inferred_year": "平台只返回月-日，年份无法确证，已标为 inferred_year，未计入年度分布",
        "relative": "平台返回相对时间（如'3 天前'），已标为 relative",
        "unknown": "无法解析的日期格式，需要人工核对",
    }
    flagged = [note[k] for k in ("inferred_year", "relative", "unknown") if confidence_counter.get(k)]
    if flagged:
        lines.append("")
        lines.append("**日期置信度提示**：")
        for text in flagged:
            lines.append(f"- {text}")
    lines.append("")
    lines.append(f"## 高互动内容 TOP {len(top_rows)}")
    lines.append("")
    lines.append("| # | 赞 | 评 | 藏 | 发布时间 | 商品位 | 标题 |")
    lines.append("| ---: | ---: | ---: | ---: | --- | --- | --- |")
    for index, row in enumerate(top_rows, start=1):
        title = str(row.get("title") or "").replace("|", "/")[:46]
        lines.append(
            f"| {index} | {num(row.get('likes')):.0f} | {num(row.get('comments')):.0f} | "
            f"{num(row.get('favorites')):.0f} | {row.get('published_at') or '-'} | "
            f"{'是' if row.get('has_product') else ''} | {title} |"
        )
    lines.append("")
    lines.append("> 以上为平台原始字段的机械统计，不含任何推断。平台口径差异（如播放量缺失）需在报告中单独说明。")

    stats_path = Path(args.stats).expanduser()
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"摘要已写出：{stats_path}")
    print(f"共 {len(rows)} 条，去重移除 {duplicates_removed} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
