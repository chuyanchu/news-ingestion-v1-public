from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from .api_server import (
    DEFAULT_DASHBOARD,
    DEFAULT_DATA_ROOT,
    PRODUCT_ROOT,
    available_date_tags,
    load_articles,
    load_sector_config,
)
from .backtest import run_backtest
from .events import build_events
from .gpt_analyzer import load_ai_analysis, provider_info
from .io_utils import read_jsonl
from .sector_heat import (
    build_sector_heatmap,
    build_trade_pool,
    dedup_articles,
    filter_recent,
    filter_sector_articles,
    resolve_sectors,
    public_heat_article,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def api_payload(data: Any, meta: dict[str, Any] | None = None, ok: bool = True) -> dict[str, Any]:
    return {"ok": ok, "data": data, "meta": meta or {}}


def date_iso(date_tag: str) -> str:
    return f"{date_tag[:4]}-{date_tag[4:6]}-{date_tag[6:]}"


def build_dates(data_root: Path, dates: list[str]) -> list[dict[str, Any]]:
    out = []
    for tag in sorted(dates, reverse=True):
        report_exists = bool((data_root / tag / f"crawl_report_{tag}.md").exists() or (data_root / tag / "crawl_report.md").exists())
        out.append({
            "date": tag,
            "date_iso": date_iso(tag),
            "article_count": len(load_articles(data_root, tag)),
            "report_exists": report_exists,
        })
    return out


def build_realtime(records: list[dict[str, Any]], sector_dict: list[dict[str, Any]], ai_map: dict[str, Any], date_tag: str, limit: int = 60) -> list[dict[str, Any]]:
    from .api_server import sort_records

    rows = dedup_articles(sort_records(filter_recent(records, date_tag), "newest"))
    data: list[dict[str, Any]] = []
    for record in rows:
        matches = resolve_sectors(record, sector_dict, ai_map)
        if not matches:
            continue
        keywords: list[str] = []
        sectors: list[str] = []
        for match in matches:
            sectors.append(match["sector"])
            keywords.extend(match["matched_keywords"])
        item = public_heat_article(record, sorted(set(keywords)), ai_map)
        item["sectors"] = sectors
        data.append(item)
        if len(data) >= limit:
            break
    return data


def build_source_health(data_root: Path, date_tag: str) -> dict[str, Any]:
    records = load_articles(data_root, date_tag)
    recent = filter_recent(records, date_tag)
    recent_ids = {id(r) for r in recent}
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        name = str(record.get("source") or "未知来源")
        group = groups.setdefault(name, {"source": name, "total": 0, "recent": 0})
        group["total"] += 1
        if id(record) in recent_ids:
            group["recent"] += 1
    errors = []
    err_path = data_root / date_tag / f"fetch_errors_{date_tag}.jsonl"
    if err_path.exists():
        for line in read_jsonl(err_path):
            errors.append({"source": line.get("source"), "url": line.get("url"), "error": line.get("error")})
    return {
        "sources": sorted(groups.values(), key=lambda x: x["recent"], reverse=True),
        "errors": errors,
        "recent_articles": len(recent),
    }


def build_stock_rank(records: list[dict[str, Any]], ai_map: dict[str, Any], date_tag: str, limit: int = 40) -> list[dict[str, Any]]:
    records = dedup_articles(filter_recent(records, date_tag))
    by_id = {str(r.get("article_id") or ""): r for r in records}
    stocks: dict[str, dict[str, Any]] = {}
    for aid, entry in ai_map.items():
        if not entry.get("enabled") or aid not in by_id:
            continue
        record = by_id[aid]
        for stock in entry.get("stocks") or []:
            name = str(stock.get("name") or "").strip()
            if not name:
                continue
            group = stocks.setdefault(name, {"name": name, "code": stock.get("code") or "", "count": 0, "importance": 0.0, "sectors": set(), "news": []})
            if not group["code"] and stock.get("code"):
                group["code"] = stock["code"]
            group["count"] += 1
            group["importance"] += float(entry.get("importance") or 0)
            for sector in entry.get("sectors") or []:
                group["sectors"].add(sector)
            if len(group["news"]) < 10:
                group["news"].append({
                    "title": record.get("title"),
                    "url": record.get("url"),
                    "source": (record.get("source_info") or {}).get("name") or record.get("source"),
                    "published_at": (record.get("time_info") or {}).get("published_at") or record.get("published_at"),
                    "impact": entry.get("impact") or "neutral",
                    "summary": entry.get("summary") or "",
                })
    ranked = sorted(stocks.values(), key=lambda x: (x["count"], x["importance"]), reverse=True)
    out = []
    for idx, group in enumerate(ranked[:limit], 1):
        out.append({
            "rank": idx,
            "name": group["name"],
            "code": group["code"],
            "count": group["count"],
            "importance": round(group["importance"] / group["count"], 1) if group["count"] else 0,
            "sectors": sorted(group["sectors"]),
            "news": group["news"],
        })
    return out


def export_static_site(data_root: Path, out_dir: Path, max_dates: int = 10) -> None:
    sector_dict = load_sector_config()
    dates = available_date_tags(data_root)[-max_dates:]
    out_data = out_dir / "static-data"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_DASHBOARD, out_dir / "index.html")

    write_json(out_data / "status.json", api_payload({
        "api_version": "static-pages",
        "latest_date": dates[-1] if dates else None,
        "refresh_requires_token": True,
        "refresh_interval_minutes": 0,
        "refresh_on_start": False,
        "data_root": "static-data",
        "running_refresh": False,
    }))
    write_json(out_data / "dates.json", api_payload(build_dates(data_root, dates), {"count": len(dates)}))
    has_precomputed_ai = False
    for tag in dates:
        if any(item.get("enabled") for item in load_ai_analysis(data_root, tag).values()):
            has_precomputed_ai = True
            break
    info = {
        "provider": "precomputed",
        "enabled": has_precomputed_ai,
        "note": "Static GitHub Pages view; browser cannot call AI directly. AI analysis is precomputed by GitHub Actions when secrets are configured.",
    }
    write_json(out_data / "gpt_status.json", api_payload(info))

    dated_records = [(tag, load_articles(data_root, tag)) for tag in dates]
    write_json(out_data / "backtest.json", api_payload(run_backtest(dated_records, sector_dict, top_n=5, pool_days=3), {"dates": dates, "top_n": 5}))

    suggestions: dict[str, dict[str, Any]] = {}
    for tag in dates[-7:]:
        id2title = {str(r.get("article_id") or ""): str(r.get("title") or "") for r in load_articles(data_root, tag)}
        for aid, entry in load_ai_analysis(data_root, tag).items():
            suggested = str(entry.get("suggested_sector") or "").strip()
            if not suggested:
                continue
            row = suggestions.setdefault(suggested, {"name": suggested, "count": 0, "samples": []})
            row["count"] += 1
            title = id2title.get(str(aid))
            if title and len(row["samples"]) < 3:
                row["samples"].append(title)
    write_json(out_data / "sector_suggestions.json", api_payload(sorted(suggestions.values(), key=lambda x: x["count"], reverse=True), {"days": 7, "dates": dates[-7:], "count": len(suggestions)}))

    for tag in dates:
        records = load_articles(data_root, tag)
        ai_map = load_ai_analysis(data_root, tag)
        heatmap = build_sector_heatmap(records, sector_dict, limit=30, as_of_date=tag, ai_map=ai_map)
        max_heat = max((float(item.get("heat_score") or 0) for item in heatmap), default=0.0)
        write_json(out_data / f"gpt_analysis_{tag}.json", api_payload([{"article_id": aid, **payload} for aid, payload in ai_map.items()], {"date": tag, "date_iso": date_iso(tag), "count": len(ai_map)}))
        write_json(out_data / f"heatmap_{tag}.json", api_payload(heatmap, {"date": tag, "date_iso": date_iso(tag), "count": len(heatmap), "max_heat_score": round(max_heat, 2)}))
        realtime = build_realtime(records, sector_dict, ai_map, tag, limit=60)
        write_json(out_data / f"realtime_{tag}.json", api_payload(realtime, {"date": tag, "date_iso": date_iso(tag), "count": len(realtime), "limit": 60}))
        for days in (1, 3, 5, 10):
            eligible = [d for d in dates if d <= tag]
            selected_dates = eligible[-days:]
            pool = build_trade_pool([(d, load_articles(data_root, d)) for d in selected_dates], sector_dict, days=days, limit=20)
            write_json(out_data / f"trade_pool_{tag}_{days}.json", api_payload(pool, {"days": days, "dates": selected_dates, "count": len(pool), "limit": 20}))
        health = build_source_health(data_root, tag)
        write_json(out_data / f"source_health_{tag}.json", api_payload({"sources": health["sources"], "errors": health["errors"]}, {"date": tag, "date_iso": date_iso(tag), "source_count": len(health["sources"]), "error_count": len(health["errors"]), "total_articles": len(records), "recent_articles": health["recent_articles"]}))
        events = build_events(dedup_articles(filter_recent(records, tag)), ai_map, sector_dict, limit=15)
        write_json(out_data / f"events_{tag}.json", api_payload(events, {"date": tag, "date_iso": date_iso(tag), "count": len(events)}))
        stocks = build_stock_rank(records, ai_map, tag, limit=40)
        write_json(out_data / f"stocks_{tag}.json", api_payload(stocks, {"date": tag, "date_iso": date_iso(tag), "count": len(stocks), "ai_analyzed": sum(1 for e in ai_map.values() if e.get("enabled"))}))
        for item in heatmap:
            sector = item["sector"]
            articles = filter_sector_articles(records, sector_dict, sector, limit=60, as_of_date=tag, ai_map=ai_map)
            for article in articles:
                article["sectors"] = [sector]
            write_json(out_data / f"sector_news_{tag}_{sector}.json", api_payload(articles, {"date": tag, "date_iso": date_iso(tag), "sector": sector, "count": len(articles)}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export static GitHub Pages dashboard.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-dir", type=Path, default=PRODUCT_ROOT / "public")
    parser.add_argument("--max-dates", type=int, default=10)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    export_static_site(args.data_root, args.out_dir, max_dates=args.max_dates)
    print(f"static_site={args.out_dir}")


if __name__ == "__main__":
    main()
