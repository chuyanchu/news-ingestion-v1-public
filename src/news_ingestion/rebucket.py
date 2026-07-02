"""按新闻真实发布日期重新归档。

采集时各源会一次性返回跨多天的新闻（尤其东财列会带前几天的内容），
原流程把它们全塞进"运行当天"的文件夹，导致日期错乱。本模块把每条新闻
写入它真实发布日期对应的 data/daily/<date>/articles_<date>.jsonl，并与
已有内容合并去重。这样用户可以按真实交易日选择查看，回测也能多出真实日期。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .io_utils import read_jsonl, write_jsonl
from .sector_heat import norm_title_key, parse_record_time

CN_TZ = ZoneInfo("Asia/Shanghai")


def article_date_tag(record: dict[str, Any]) -> str | None:
    """返回记录真实发布日期的 YYYYMMDD，解析不出则 None。"""
    parsed = parse_record_time(record)
    return parsed.strftime("%Y%m%d") if parsed else None


def _dedup_by_identity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 article_id + 归一化标题去重，保留先出现的（调用方把新数据放前面）。"""
    seen_id: set[str] = set()
    seen_title: set[str] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        aid = str(record.get("article_id") or "")
        title_key = norm_title_key(record)
        if aid and aid in seen_id:
            continue
        if title_key and title_key in seen_title:
            continue
        if aid:
            seen_id.add(aid)
        if title_key:
            seen_title.add(title_key)
        out.append(record)
    return out


def rebucket_by_date(
    records: list[dict[str, Any]],
    out_root: Path,
    *,
    window_days: int = 10,
    as_of_tag: str | None = None,
) -> dict[str, int]:
    """把 records 按真实发布日期写入各自的 articles_<date>.jsonl（合并去重）。

    只处理 as_of 往前 window_days 天内的日期，避免给很旧的零散文章建一堆目录。
    返回 {date_tag: 该日合并后的条数}。
    """
    if as_of_tag:
        try:
            as_of = datetime.strptime(as_of_tag, "%Y%m%d").replace(tzinfo=CN_TZ)
        except ValueError:
            as_of = datetime.now(CN_TZ)
    else:
        as_of = datetime.now(CN_TZ)
    cutoff = as_of - timedelta(days=window_days)
    upper = as_of + timedelta(days=1)

    by_date: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        parsed = parse_record_time(record)
        if parsed is None or parsed < cutoff or parsed > upper:
            continue
        by_date.setdefault(parsed.strftime("%Y%m%d"), []).append(record)

    written: dict[str, int] = {}
    for tag, day_records in by_date.items():
        folder = out_root / tag
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"articles_{tag}.jsonl"
        existing = list(read_jsonl(path)) if path.exists() else []
        merged = _dedup_by_identity(day_records + existing)  # 新数据在前，并列时保留新的
        write_jsonl(path, merged)
        written[tag] = len(merged)
    return written
