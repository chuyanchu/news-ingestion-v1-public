from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_MAX_AGE_DAYS = 2  # "今日"视图只保留发布日期在近 N 天内的新闻，过滤东财列混入的旧闻
IMPORTANT_WORDS = [
    "政策",
    "重磅",
    "突破",
    "首个",
    "订单",
    "中标",
    "涨价",
    "降价",
    "并购",
    "重组",
    "业绩",
    "预增",
    "投产",
    "扩产",
    "获批",
    "监管",
    "制裁",
    "关税",
]
NEGATIVE_WORDS = ["下滑", "亏损", "处罚", "调查", "风险", "召回", "暴跌", "减持", "禁令"]
POSITIVE_WORDS = ["增长", "突破", "中标", "签约", "获批", "投产", "扩产", "预增", "回购", "涨价"]


def load_sector_dictionary(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sectors = data.get("sectors", [])
    return sectors if isinstance(sectors, list) else []


def compact_text(value: Any, max_length: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if max_length is not None:
        return text[:max_length]
    return text


_LEADING_TAG_RE = re.compile(r"^[【\[][^】\]]*[】\]]")
_PUNCT_RE = re.compile(r"[\s　：:，,。.\"""''！!？?、\-—()（）]+")


def norm_title_key(record: dict[str, Any]) -> str:
    """把标题归一化成去重键：去掉开头的【来源/标签】、去掉空白和标点、转小写、取前30字。

    用于跨源近似去重——同一条快讯被财联社、东方财富、新浪同时登出时，
    虽然 title_hash 不同（前缀【】或标点有差异），归一化后能识别为同一条。
    """
    title = str(record.get("title") or "")
    title = _LEADING_TAG_RE.sub("", title)
    title = _PUNCT_RE.sub("", title)
    return title.lower()[:30]


def filter_recent(
    records: list[dict[str, Any]],
    as_of_tag: str | None,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> list[dict[str, Any]]:
    """只保留发布日期在 [as_of - max_age_days, as_of+1天] 内的新闻。

    东方财富的财联社等列会混入几个月前的非时效旧文章，污染当日热度和时间线。
    as_of_tag 形如 'YYYYMMDD'；无法解析或无 as_of 时原样返回；无发布时间的记录保留。
    """
    if not as_of_tag:
        return records
    try:
        as_of = datetime.strptime(as_of_tag, "%Y%m%d").replace(tzinfo=CN_TZ)
    except ValueError:
        return records
    cutoff = as_of - timedelta(days=max_age_days)
    upper = as_of + timedelta(days=1)  # 容忍当日及轻微时区误差
    out: list[dict[str, Any]] = []
    for record in records:
        published = parse_record_time(record)
        if published is None or cutoff <= published <= upper:
            out.append(record)
    return out


def dedup_articles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按归一化标题去重，保留输入顺序中的第一条。

    调用方应先按需要的优先级排序（如最新优先 / 热度优先），再去重，
    这样保留下来的就是该优先级下的代表条目。无标题的记录原样保留。
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        key = norm_title_key(record)
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(record)
    return out


def record_text(record: dict[str, Any]) -> str:
    content_info = record.get("content_info") or {}
    pieces = [
        record.get("title"),
        record.get("content"),
        content_info.get("excerpt"),
        " ".join(record.get("keywords") or []),
    ]
    return compact_text(" ".join(str(piece or "") for piece in pieces))


def parse_record_time(record: dict[str, Any]) -> datetime | None:
    raw = (
        (record.get("time_info") or {}).get("published_at")
        or record.get("published_at")
        or record.get("crawled_at")
    )
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def hours_since(record: dict[str, Any], now: datetime | None = None) -> float:
    parsed = parse_record_time(record)
    if not parsed:
        return 24.0
    now = now or datetime.now(CN_TZ)
    return max(0.0, (now - parsed).total_seconds() / 3600)


def article_hot_score(record: dict[str, Any]) -> float:
    hotness = record.get("hotness") or {}
    if isinstance(hotness.get("score"), (int, float)):
        base = float(hotness["score"])
    else:
        quality = float(record.get("quality_score") or 0)
        source_priority = float((record.get("source_info") or {}).get("priority") or 0)
        base = quality * 30 + source_priority * 3

    engagement = record.get("engagement") or {}
    engagement_score = float(engagement.get("score") or 0)
    recency_bonus = max(0.0, 18.0 - min(hours_since(record), 48.0) * 0.4)
    title = str(record.get("title") or "")
    important_bonus = sum(4.0 for word in IMPORTANT_WORDS if word in title)
    return round(base + engagement_score * 2.5 + recency_bonus + important_bonus, 4)


# Cache compiled matchers for ASCII aliases so we don't recompile per record.
_ALIAS_MATCHER_CACHE: dict[str, re.Pattern[str]] = {}


def _alias_matcher(alias: str) -> re.Pattern[str]:
    """Build a case-sensitive, ASCII-letter-bounded matcher for short English aliases.

    Plain substring matching is dangerous for English/abbreviation aliases:
    e.g. "AI" lowercased matches "Shanghai"/"main"/"retail", polluting sector heat.
    For ASCII aliases we require:
      - case sensitivity ("AI" must not match "ai" inside "wait")
      - no adjacent ASCII letter on either side (so "CPO光模块" still matches,
        but "feedax" never matches "EDA")
    CJK aliases keep simple substring matching (Chinese has no word-boundary issue).
    """
    cached = _ALIAS_MATCHER_CACHE.get(alias)
    if cached is None:
        cached = re.compile(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])")
        _ALIAS_MATCHER_CACHE[alias] = cached
    return cached


def match_sectors(record: dict[str, Any], sector_dict: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_text = record_text(record)  # keep original case for ASCII boundary matching
    lower_text = raw_text.lower()
    matches: list[dict[str, Any]] = []
    for sector in sector_dict:
        name = str(sector.get("name") or "").strip()
        aliases = [name] + [str(item) for item in (sector.get("aliases") or [])]
        hit_words = []
        for alias in aliases:
            alias_text = alias.strip()
            if not alias_text:
                continue
            if alias_text.isascii():
                # English / abbreviation alias → strict, case-sensitive, bounded
                if _alias_matcher(alias_text).search(raw_text):
                    hit_words.append(alias_text)
            else:
                # CJK alias → substring is safe
                if alias_text.lower() in lower_text:
                    hit_words.append(alias_text)
        if hit_words:
            matches.append({"sector": name, "matched_keywords": sorted(set(hit_words))})
    return matches


_ALIAS_INDEX_CACHE: dict[tuple, dict[str, str]] = {}


def _alias_index(sector_dict: list[dict[str, Any]]) -> dict[str, str]:
    """构建 别名(小写) -> 规范板块名 的索引，用于把 AI 返回的板块名对齐到 14 类。"""
    key = tuple(str(s.get("name") or "") for s in sector_dict)
    idx = _ALIAS_INDEX_CACHE.get(key)
    if idx is None:
        idx = {}
        for sector in sector_dict:
            name = str(sector.get("name") or "").strip()
            if not name:
                continue
            idx[name.lower()] = name
            for alias in sector.get("aliases") or []:
                idx[str(alias).strip().lower()] = name
        _ALIAS_INDEX_CACHE[key] = idx
    return idx


def resolve_sectors(
    record: dict[str, Any],
    sector_dict: list[dict[str, Any]],
    ai_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """决定一条新闻归属哪些板块：优先用 AI 判断，无 AI 结果则回退关键词匹配。

    AI 判断的板块名会归一化到我们的 14 类（不在列表里的丢弃）；
    展示用的 matched_keywords 仍尽量从关键词命中里取，方便热力图显示。
    """
    kw_matches = match_sectors(record, sector_dict)
    if ai_map:
        ai = ai_map.get(str(record.get("article_id") or ""))
        if ai and ai.get("enabled") and ai.get("sectors"):
            idx = _alias_index(sector_dict)
            kw_map = {m["sector"]: m["matched_keywords"] for m in kw_matches}
            seen: set[str] = set()
            out: list[dict[str, Any]] = []
            for raw in ai["sectors"]:
                canon = idx.get(str(raw).strip().lower())
                if canon and canon not in seen:
                    seen.add(canon)
                    out.append({"sector": canon, "matched_keywords": kw_map.get(canon, []), "by": "ai"})
            if out:
                return out
    return [dict(m, by="keyword") for m in kw_matches]


def infer_impact(record: dict[str, Any]) -> str:
    text = record_text(record)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    if negative > positive:
        return "negative"
    if positive > negative:
        return "positive"
    return "neutral"


def article_summary(record: dict[str, Any]) -> str:
    content_info = record.get("content_info") or {}
    content = content_info.get("excerpt") or record.get("content") or record.get("title") or ""
    return compact_text(content, 180)


def public_heat_article(
    record: dict[str, Any],
    matched_keywords: list[str],
    ai_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_info = record.get("source_info") or {}
    time_info = record.get("time_info") or {}
    title = compact_text(record.get("title"))
    importance_score = article_hot_score(record)
    article = {
        "article_id": record.get("article_id"),
        "title": title,
        "source": source_info.get("name") or record.get("source"),
        "source_id": source_info.get("id") or record.get("source_id"),
        "url": record.get("url"),
        "published_at": time_info.get("published_at") or record.get("published_at"),
        "summary": article_summary(record),
        "importance_score": round(importance_score, 2),
        "impact": infer_impact(record),
        "catalyst": "",
        "ai": False,
        "matched_keywords": matched_keywords,
    }
    return overlay_ai(article, ai_map)


def overlay_ai(article: dict[str, Any], ai_map: dict[str, Any] | None) -> dict[str, Any]:
    """用持久化的 AI 分析覆盖文章的情绪/重要性/摘要，没有则保持关键词版。"""
    if not ai_map:
        return article
    ai = ai_map.get(str(article.get("article_id") or ""))
    if ai and ai.get("enabled"):
        if ai.get("summary"):
            article["summary"] = ai["summary"]
        if ai.get("impact") in {"positive", "negative", "neutral"}:
            article["impact"] = ai["impact"]
        if ai.get("importance") is not None:
            article["importance_score"] = float(ai["importance"])
        if ai.get("catalyst"):
            article["catalyst"] = ai["catalyst"]
        article["ai"] = True
    return article


def effective_impact(record: dict[str, Any], ai_map: dict[str, Any] | None) -> str:
    """优先用 AI 情绪，回退到关键词推断。"""
    if ai_map:
        ai = ai_map.get(str(record.get("article_id") or ""))
        if ai and ai.get("enabled") and ai.get("impact") in {"positive", "negative", "neutral"}:
            return ai["impact"]
    return infer_impact(record)


def build_sector_heatmap(
    records: list[dict[str, Any]],
    sector_dict: list[dict[str, Any]],
    *,
    limit: int = 50,
    important_limit: int = 5,
    as_of_date: str | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ai_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    records = filter_recent(records, as_of_date, max_age_days=max_age_days)  # 过滤旧闻
    records = dedup_articles(records)  # 跨源去重，避免同一条快讯重复计入板块热度
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("status") not in {None, "valid", "review"}:
            continue
        matches = resolve_sectors(record, sector_dict, ai_map)  # AI 判断优先，关键词兜底
        if not matches:
            continue
        score = article_hot_score(record)
        # 一条新闻命中多个板块时，热度按命中数分摊，避免同一条新闻把相关板块热度重复抬高。
        # 条数仍按"提及"计：每个命中板块 +1（即 news_count = 提及该板块的新闻数）。
        share = score / len(matches)
        for match in matches:
            sector_name = match["sector"]
            group = groups.setdefault(
                sector_name,
                {
                    "sector": sector_name,
                    "heat_score": 0.0,
                    "news_count": 0,
                    "important_count": 0,
                    "positive_count": 0,
                    "negative_count": 0,
                    "latest_published_at": None,
                    "matched_keywords": set(),
                    "articles": [],
                },
            )
            group["heat_score"] += share
            group["news_count"] += 1
            ai_entry = ai_map.get(str(record.get("article_id") or "")) if ai_map else None
            ai_important = bool(ai_entry and ai_entry.get("enabled") and float(ai_entry.get("importance") or 0) >= 60)
            if ai_important or score >= 55 or any(word in str(record.get("title") or "") for word in IMPORTANT_WORDS):
                group["important_count"] += 1
            impact = effective_impact(record, ai_map)
            if impact == "positive":
                group["positive_count"] += 1
            elif impact == "negative":
                group["negative_count"] += 1
            published_at = (record.get("time_info") or {}).get("published_at") or record.get("published_at")
            if published_at and (not group["latest_published_at"] or str(published_at) > str(group["latest_published_at"])):
                group["latest_published_at"] = published_at
            group["matched_keywords"].update(match["matched_keywords"])
            group["articles"].append(public_heat_article(record, match["matched_keywords"], ai_map))

    ranked = []
    for group in groups.values():
        articles = sorted(group["articles"], key=lambda item: item["importance_score"], reverse=True)
        raw_score = group["heat_score"] + group["important_count"] * 8 + math.log1p(group["news_count"]) * 12
        ranked.append(
            {
                "sector": group["sector"],
                "heat_score": round(raw_score, 2),
                "news_count": group["news_count"],
                "important_count": group["important_count"],
                "positive_count": group["positive_count"],
                "negative_count": group["negative_count"],
                "latest_published_at": group["latest_published_at"],
                "matched_keywords": sorted(group["matched_keywords"]),
                "top_articles": articles[:important_limit],
            }
        )
    ranked.sort(key=lambda item: item["heat_score"], reverse=True)
    for idx, item in enumerate(ranked, 1):
        item["rank"] = idx
    return ranked[:limit]


def filter_sector_articles(
    records: list[dict[str, Any]],
    sector_dict: list[dict[str, Any]],
    sector_name: str,
    *,
    limit: int = 100,
    as_of_date: str | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    ai_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    records = filter_recent(records, as_of_date, max_age_days=max_age_days)
    records = dedup_articles(records)
    articles: list[dict[str, Any]] = []
    for record in records:
        for match in resolve_sectors(record, sector_dict, ai_map):
            if match["sector"] == sector_name:
                articles.append(public_heat_article(record, match["matched_keywords"], ai_map))
                break
    articles.sort(key=lambda item: item["importance_score"], reverse=True)
    return articles[:limit]


def build_trade_pool(
    dated_records: list[tuple[str, list[dict[str, Any]]]],
    sector_dict: list[dict[str, Any]],
    *,
    days: int = 3,
    limit: int = 20,
) -> list[dict[str, Any]]:
    sector_days: dict[str, dict[str, Any]] = {}
    for date_tag, records in dated_records[-days:]:
        heatmap = build_sector_heatmap(records, sector_dict, limit=200, important_limit=3)
        for item in heatmap:
            group = sector_days.setdefault(
                item["sector"],
                {
                    "sector": item["sector"],
                    "active_days": 0,
                    "total_heat": 0.0,
                    "total_news_count": 0,
                    "important_count": 0,
                    "dates": [],
                    "top_articles": [],
                },
            )
            group["active_days"] += 1
            group["total_heat"] += item["heat_score"]
            group["total_news_count"] += item["news_count"]
            group["important_count"] += item["important_count"]
            group["dates"].append({"date": date_tag, "heat_score": item["heat_score"], "news_count": item["news_count"]})
            group["top_articles"].extend(item["top_articles"][:2])

    pool = []
    for group in sector_days.values():
        continuity_score = group["active_days"] * 18 + group["total_heat"] / max(group["active_days"], 1) + group["important_count"] * 5
        top_articles = sorted(group["top_articles"], key=lambda item: item["importance_score"], reverse=True)
        pool.append(
            {
                "sector": group["sector"],
                "pool_score": round(continuity_score, 2),
                "active_days": group["active_days"],
                "total_news_count": group["total_news_count"],
                "important_count": group["important_count"],
                "dates": group["dates"],
                "reason": f"近{days}天内{group['active_days']}天有消息催化，累计相关新闻{group['total_news_count']}条。",
                "top_articles": top_articles[:5],
            }
        )
    pool.sort(key=lambda item: item["pool_score"], reverse=True)
    for idx, item in enumerate(pool, 1):
        item["rank"] = idx
    return pool[:limit]
