"""轻量热点事件聚类。

把同一天里讲同一件事的新闻（被财联社/东财/新浪各报一条）聚成一个"事件"，
不依赖 BERTopic 等重模型。聚类信号：
  1. 共享个股（两条都点名"寒武纪" → 大概率同一事件）——最强信号，来自 AI 抽取的 stocks
  2. 标题字符二元组相似度（Jaccard）——兜底

输出"今日热点事件 Top N"：每个事件有代表标题、报道数、来源、涉及个股/板块、最高重要性。
"""
from __future__ import annotations

import re
from typing import Any

from .sector_heat import article_hot_score, compact_text, resolve_sectors

_PUNCT = re.compile(r"[\s　：:，,。.\"""''！!？?、（）()\[\]【】·\-—|/]+")


def _title_bigrams(title: str) -> set[str]:
    t = _PUNCT.sub("", title)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else {t}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def build_events(
    records: list[dict[str, Any]],
    ai_map: dict[str, Any],
    sector_dict: list[dict[str, Any]],
    *,
    limit: int = 15,
    sim_threshold: float = 0.34,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") not in {None, "valid", "review"}:
            continue
        aid = str(record.get("article_id") or "")
        ai = ai_map.get(aid) or {}
        stocks = {str(s.get("name")) for s in (ai.get("stocks") or []) if s.get("name")}
        sectors = {m["sector"] for m in resolve_sectors(record, sector_dict, ai_map)}
        title = compact_text(record.get("title"))
        importance = float(ai.get("importance") or 0) if ai.get("enabled") else article_hot_score(record)
        items.append({
            "aid": aid, "title": title, "tokens": _title_bigrams(title),
            "stocks": stocks, "sectors": sectors, "importance": importance,
            "source": (record.get("source_info") or {}).get("name") or record.get("source"),
            "url": record.get("url"),
            "published_at": (record.get("time_info") or {}).get("published_at") or record.get("published_at"),
            "impact": ai.get("impact") or "neutral",
            "summary": ai.get("summary") or "",
        })

    # 重要性高的先作为各簇的代表
    items.sort(key=lambda x: x["importance"], reverse=True)
    clusters: list[dict[str, Any]] = []
    for it in items:
        placed = None
        focused = 1 <= len(it["stocks"]) <= 3  # 综述类(点名很多股)不靠共享个股合并
        for cluster in clusters:
            # 只跟"簇代表"比对，避免 A-B-C 链式传染过度合并
            shared_stock = focused and cluster["repr_focused"] and bool(it["stocks"] & cluster["repr_stocks"])
            similar = _jaccard(it["tokens"], cluster["repr_tokens"]) >= sim_threshold
            if shared_stock or similar:
                placed = cluster
                break
        if placed is None:
            clusters.append({
                "repr_tokens": it["tokens"], "repr_title": it["title"],
                "repr_stocks": set(it["stocks"]), "repr_focused": focused,
                "stocks": set(it["stocks"]), "sectors": set(it["sectors"]),
                "importance": it["importance"], "members": [it],
            })
        else:
            placed["stocks"] |= it["stocks"]
            placed["sectors"] |= it["sectors"]
            placed["importance"] = max(placed["importance"], it["importance"])
            placed["members"].append(it)

    events = []
    for cluster in clusters:
        members = sorted(cluster["members"], key=lambda x: x["importance"], reverse=True)
        sources = sorted({m["source"] for m in members if m["source"]})
        events.append({
            "title": cluster["repr_title"],
            "report_count": len(members),
            "source_count": len(sources),
            "sources": sources,
            "stocks": sorted(cluster["stocks"]),
            "sectors": sorted(cluster["sectors"]),
            "importance": round(cluster["importance"], 1),
            "impact": members[0]["impact"],
            "summary": members[0]["summary"],
            "articles": [
                {"title": m["title"], "url": m["url"], "source": m["source"],
                 "published_at": m["published_at"], "impact": m["impact"]}
                for m in members[:8]
            ],
        })
    # 排序：多源报道 + 重要性高的排前
    events.sort(key=lambda e: (e["report_count"], e["importance"]), reverse=True)
    for idx, event in enumerate(events[:limit], 1):
        event["rank"] = idx
    return events[:limit]
