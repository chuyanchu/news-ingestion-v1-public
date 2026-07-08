from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

from .cli import DEFAULT_REGISTRY, DEFAULT_RULES_PATH, PRODUCT_ROOT, daily_collect, date_tags, load_registry
from .gpt_analyzer import (
    ai_enabled,
    analyze_article_with_gpt,
    batch_analyze_for_date,
    gpt_enabled,
    load_ai_analysis,
    provider_info,
    save_ai_analysis,
)
from .io_utils import read_jsonl
from .sector_heat import (
    build_sector_heatmap,
    build_trade_pool,
    dedup_articles,
    _alias_index,
    filter_recent,
    filter_sector_articles,
    load_sector_dictionary,
    match_sectors,
    public_heat_article,
    resolve_sectors,
)
from .backtest import run_backtest
from .events import build_events


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DATA_ROOT = PRODUCT_ROOT / "data" / "daily"
DEFAULT_INBOX_DIR = PRODUCT_ROOT / "data" / "inbox"
DEFAULT_SECTOR_KEYWORDS = PRODUCT_ROOT / "config" / "sector_keywords.v1.json"
DEFAULT_DASHBOARD = PRODUCT_ROOT / "static" / "dashboard.html"
API_VERSION = "v1"
DEFAULT_REFRESH_INTERVAL_SECONDS = 10
EXPORT_METRIC_FIELDS = ["read_count", "view_count", "comment_count", "like_count", "favorite_count", "share_count", "repost_count"]
EXPORT_FIELDNAMES = [
    "schema_version",
    "article_id",
    "title",
    "source_name",
    "source_id",
    "source_tier",
    "source_role",
    "source_section",
    "published_at",
    "published_date",
    "crawled_at",
    "age_minutes_at_crawl",
    "author",
    "status",
    "quality_score",
    "quality_grade",
    "review_required",
    "quality_flags",
    "hot_score",
    "list_rank",
    "list_size",
    "rank_percentile",
    "source_priority",
    "has_engagement_metrics",
    "available_metric_count",
    "engagement_score",
    "metric_source",
    "engagement_available_metrics",
    "read_count",
    "view_count",
    "comment_count",
    "like_count",
    "favorite_count",
    "share_count",
    "repost_count",
    "url",
    "content_length",
    "content_excerpt",
]


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat()


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: str | None, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def normalize_date(value: str | None, data_root: Path) -> tuple[str, str]:
    if value:
        return date_tags(value)
    latest = latest_date_tag(data_root)
    if latest:
        return date_tags(latest)
    return date_tags(None)


def latest_date_tag(data_root: Path) -> str | None:
    if not data_root.exists():
        return None
    tags = [
        item.name
        for item in data_root.iterdir()
        if item.is_dir() and len(item.name) == 8 and item.name.isdigit()
    ]
    return sorted(tags)[-1] if tags else None


def load_articles(data_root: Path, date_tag: str) -> list[dict[str, Any]]:
    day_dir = data_root / date_tag
    candidates = [
        day_dir / f"articles_{date_tag}.jsonl",
        day_dir / "articles_valid.jsonl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return read_jsonl(candidate)
    return []


def load_dashboard_html() -> str:
    if DEFAULT_DASHBOARD.exists():
        return DEFAULT_DASHBOARD.read_text(encoding="utf-8")
    return "<!doctype html><title>News Dashboard</title><p>dashboard.html not found</p>"


def load_sector_config() -> list[dict[str, Any]]:
    return load_sector_dictionary(DEFAULT_SECTOR_KEYWORDS)


def available_date_tags(data_root: Path) -> list[str]:
    if not data_root.exists():
        return []
    return sorted(
        item.name
        for item in data_root.iterdir()
        if item.is_dir() and len(item.name) == 8 and item.name.isdigit()
    )


def load_report(data_root: Path, date_tag: str) -> str:
    day_dir = data_root / date_tag
    candidates = [
        day_dir / f"crawl_report_{date_tag}.md",
        day_dir / "crawl_report.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return ""


def parse_record_time(record: dict[str, Any]) -> datetime | None:
    raw = record.get("published_at") or record.get("crawled_at")
    if not raw:
        return None
    if isinstance(raw, str) and raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def hot_rank_score(record: dict[str, Any]) -> float:
    hotness = record.get("hotness") or {}
    if isinstance(hotness.get("score"), (int, float)):
        return float(hotness["score"])
    hot_features = record.get("hot_features") or {}
    prominence = hot_features.get("source_prominence") or {}
    metrics = hot_features.get("engagement_metrics") or {}
    quality = float(record.get("quality_score") or 0)
    source_priority = float(prominence.get("source_priority") or 0)
    rank = prominence.get("list_rank")
    size = prominence.get("list_size")

    score = quality * 20 + source_priority * 4
    if isinstance(rank, int) and isinstance(size, int) and size > 0:
        score += max(0.0, (size - rank + 1) / size) * 35

    metric_weights = {
        "read_count": 1.4,
        "view_count": 1.2,
        "comment_count": 5.0,
        "like_count": 2.0,
        "favorite_count": 2.2,
        "share_count": 3.0,
        "repost_count": 3.5,
    }
    for field, weight in metric_weights.items():
        value = metrics.get(field)
        if isinstance(value, int) and value > 0:
            score += math.log10(value + 1) * weight

    parsed_time = parse_record_time(record)
    if parsed_time:
        age_hours = max(0.0, (datetime.now(CN_TZ) - parsed_time).total_seconds() / 3600)
        score += max(0.0, 20.0 - min(age_hours, 48.0) * 0.35)

    return round(score, 4)


def public_article(record: dict[str, Any], include_content: bool = False) -> dict[str, Any]:
    content = record.get("content") or ""
    item = {
        "schema_version": record.get("schema_version"),
        "record_type": record.get("record_type"),
        "article_id": record.get("article_id"),
        "title": record.get("title"),
        "source": record.get("source"),
        "source_id": record.get("source_id"),
        "source_tier": record.get("source_tier"),
        "url": record.get("url"),
        "published_at": record.get("published_at"),
        "crawled_at": record.get("crawled_at"),
        "author": record.get("author"),
        "section": record.get("section"),
        "keywords": record.get("keywords") or [],
        "status": record.get("status"),
        "quality_score": record.get("quality_score"),
        "quality_flags": record.get("quality_flags") or [],
        "quality": record.get("quality") or {},
        "hot_rank_score": hot_rank_score(record),
        "hotness": record.get("hotness") or {},
        "engagement": record.get("engagement") or {},
        "source_info": record.get("source_info") or {},
        "time_info": record.get("time_info") or {},
        "content_info": record.get("content_info") or {},
        "extraction": record.get("extraction") or {},
        "hot_features": record.get("hot_features") or {},
        "content_excerpt": content[:240],
    }
    if include_content:
        item["content"] = content
    return item


def compact_text(value: Any, max_length: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if max_length is not None:
        return text[:max_length]
    return text


def export_article(record: dict[str, Any], include_content: bool = False) -> dict[str, Any]:
    content = record.get("content") or ""
    source_info = record.get("source_info") or {}
    time_info = record.get("time_info") or {}
    content_info = record.get("content_info") or {}
    quality = record.get("quality") or {}
    hotness = record.get("hotness") or {}
    engagement = record.get("engagement") or {}
    hot_features = record.get("hot_features") or {}
    prominence = hot_features.get("source_prominence") or {}
    metrics = hot_features.get("engagement_metrics") or {}
    counts = engagement.get("counts") or {}
    item = {
        "schema_version": record.get("schema_version"),
        "article_id": record.get("article_id"),
        "title": compact_text(record.get("title")),
        "source_name": compact_text(source_info.get("name") or record.get("source")),
        "source_id": source_info.get("id") or record.get("source_id"),
        "source_tier": source_info.get("tier") or record.get("source_tier"),
        "source_role": source_info.get("role"),
        "source_section": source_info.get("section") or compact_text(record.get("section")),
        "published_at": time_info.get("published_at") or record.get("published_at"),
        "published_date": time_info.get("published_date"),
        "crawled_at": time_info.get("crawled_at") or record.get("crawled_at"),
        "age_minutes_at_crawl": time_info.get("age_minutes_at_crawl"),
        "author": compact_text(record.get("author")),
        "status": record.get("status"),
        "quality_score": record.get("quality_score"),
        "quality_grade": quality.get("grade"),
        "review_required": quality.get("review_required"),
        "quality_flags": ";".join(quality.get("flags") or record.get("quality_flags") or []),
        "hot_score": hotness.get("score") if hotness else hot_rank_score(record),
        "list_rank": hotness.get("list_rank") if hotness else prominence.get("list_rank"),
        "list_size": hotness.get("list_size") if hotness else prominence.get("list_size"),
        "rank_percentile": hotness.get("rank_percentile"),
        "source_priority": hotness.get("source_priority") if hotness else prominence.get("source_priority"),
        "has_engagement_metrics": engagement.get("has_any_metric"),
        "available_metric_count": engagement.get("available_metric_count"),
        "engagement_score": engagement.get("score"),
        "metric_source": engagement.get("metric_source") or metrics.get("source"),
        "engagement_available_metrics": ";".join(engagement.get("available_metrics") or metrics.get("available_fields") or []),
        "url": record.get("url"),
        "content_length": content_info.get("content_length") if content_info else len(content),
        "content_excerpt": compact_text(content_info.get("excerpt") or content, 300),
    }
    for field in EXPORT_METRIC_FIELDS:
        item[field] = counts.get(field) if counts else metrics.get(field)
    if include_content:
        item["content"] = compact_text(content)
    return item


def csv_text(records: list[dict[str, Any]], include_content: bool = False) -> str:
    output = io.StringIO()
    fieldnames = EXPORT_FIELDNAMES + (["content"] if include_content else [])
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow(export_article(record, include_content=include_content))
    return output.getvalue()


def jsonl_text(records: list[dict[str, Any]], include_content: bool = False, flattened: bool = False) -> str:
    rows = [export_article(record, include_content=include_content) if flattened else public_article(record, include_content=include_content) for record in records]
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def xlsx_bytes(records: list[dict[str, Any]], include_content: bool = False, sheet_name: str = "articles") -> bytes:
    headers = EXPORT_FIELDNAMES + (["content"] if include_content else [])
    rows = [headers]
    for record in records:
        exported = export_article(record, include_content=include_content)
        rows.append([exported.get(header) for header in headers])

    sheet_rows: list[str] = []
    for row_idx, row in enumerate(rows, 1):
        cells: list[str] = []
        for col_idx, value in enumerate(row, 1):
            ref = f"{column_letter(col_idx)}{row_idx}"
            if value is None:
                cells.append(f'<c r="{ref}"/>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = xml_escape(str(value))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{xml_escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return buffer.getvalue()


def date_filename(prefix: str, date_tag: str, extension: str) -> str:
    return f"{prefix}_{date_tag}.{extension}"


def filter_records(records: list[dict[str, Any]], params: dict[str, list[str]]) -> list[dict[str, Any]]:
    source = first_param(params, "source")
    source_id = first_param(params, "source_id")
    status = first_param(params, "status")
    tier = first_param(params, "source_tier")
    query = (first_param(params, "q") or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for record in records:
        if source and str(record.get("source") or "") != source:
            continue
        if source_id and str(record.get("source_id") or "") != source_id:
            continue
        if status and str(record.get("status") or "") != status:
            continue
        if tier and str(record.get("source_tier") or "") != tier:
            continue
        if query:
            haystack = " ".join(
                str(record.get(key) or "")
                for key in ("title", "content", "source", "section")
            ).lower()
            if query not in haystack:
                continue
        filtered.append(record)
    return filtered


def sort_records(records: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    if sort_key == "hot":
        return sorted(records, key=hot_rank_score, reverse=True)
    if sort_key == "quality":
        return sorted(records, key=lambda item: float(item.get("quality_score") or 0), reverse=True)
    if sort_key == "rank":
        return sorted(
            records,
            key=lambda item: (
                item.get("hot_features", {}).get("source_prominence", {}).get("list_rank") is None,
                item.get("hot_features", {}).get("source_prominence", {}).get("list_rank") or 999999,
            ),
        )
    return sorted(records, key=lambda item: item.get("published_at") or item.get("crawled_at") or "", reverse=True)


def first_param(params: dict[str, list[str]], key: str, default: str | None = None) -> str | None:
    values = params.get(key)
    if not values:
        return default
    return values[0]


class ApiState:
    def __init__(
        self,
        data_root: Path,
        inbox_dir: Path,
        registry_path: Path,
        rules_path: Path,
        refresh_interval_seconds: int,
        refresh_on_start: bool,
    ) -> None:
        self.data_root = data_root
        self.inbox_dir = inbox_dir
        self.registry_path = registry_path
        self.rules_path = rules_path
        self.refresh_interval_seconds = refresh_interval_seconds
        self.refresh_interval_minutes = round(refresh_interval_seconds / 60, 4)
        self.refresh_on_start = refresh_on_start
        self.lock = threading.RLock()
        self.started_at = now_iso()
        self.last_refresh_started_at: str | None = None
        self.last_refresh_completed_at: str | None = None
        self.last_refresh_error: str | None = None
        self.refresh_count = 0
        self.running_refresh = False
        self.last_ai_stats: dict[str, Any] | None = None
        self.stop_event = threading.Event()

    def start_background_refresh(self) -> None:
        if not self.refresh_on_start and self.refresh_interval_seconds <= 0:
            return
        thread = threading.Thread(target=self._background_loop, name="news-api-refresh", daemon=True)
        thread.start()

    def _background_loop(self) -> None:
        if self.refresh_on_start:
            self.refresh_once(reason="startup")
        while self.refresh_interval_seconds > 0 and not self.stop_event.wait(self.refresh_interval_seconds):
            self.refresh_once(reason="interval")

    def refresh_once(self, date: str | None = None, reason: str = "manual") -> dict[str, Any]:
        with self.lock:
            self.running_refresh = True
            self.last_refresh_started_at = now_iso()
            self.last_refresh_error = None
            try:
                daily_collect(
                    out_root=self.data_root,
                    inbox_dir=self.inbox_dir,
                    registry_path=self.registry_path,
                    rules_path=self.rules_path,
                    date=date,
                    fetch_rss=True,
                    fetch_html=True,
                )
                self.refresh_count += 1
                self.last_refresh_completed_at = now_iso()
                resolved_date = date_tags(date)[0] if date else date_tags(None)[0]
                # 采集完成后，后台异步对当天命中板块的新闻跑 AI（不阻塞本次刷新返回）
                if ai_enabled():
                    threading.Thread(
                        target=self._analyze_in_background, args=(resolved_date,),
                        name="news-api-ai", daemon=True,
                    ).start()
                return {
                    "reason": reason,
                    "date": resolved_date,
                    "completed_at": self.last_refresh_completed_at,
                    "refresh_count": self.refresh_count,
                }
            except Exception as exc:  # noqa: BLE001
                self.last_refresh_error = str(exc)
                raise
            finally:
                self.running_refresh = False

    def _analyze_in_background(self, date_tag: str) -> None:
        """后台线程：对某日全部命中板块的新闻跑 AI（增量+缓存）。"""
        try:
            sector_dict = load_sector_config()
            records = load_articles(self.data_root, date_tag)
            stats = batch_analyze_for_date(records, sector_dict, self.data_root, date_tag)
            self.last_ai_stats = {"date": date_tag, "at": now_iso(), **stats}
        except Exception as exc:  # noqa: BLE001
            self.last_ai_stats = {"date": date_tag, "at": now_iso(), "error": str(exc)}

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "api_version": API_VERSION,
                "started_at": self.started_at,
                "data_root": str(self.data_root),
                "latest_date": latest_date_tag(self.data_root),
                "refresh_interval_seconds": self.refresh_interval_seconds,
                "refresh_interval_minutes": self.refresh_interval_minutes,
                "refresh_on_start": self.refresh_on_start,
                "refresh_requires_token": bool(os.environ.get("NEWS_API_TOKEN")),
                "running_refresh": self.running_refresh,
                "last_refresh_started_at": self.last_refresh_started_at,
                "last_refresh_completed_at": self.last_refresh_completed_at,
                "last_refresh_error": self.last_refresh_error,
                "refresh_count": self.refresh_count,
                "last_ai_stats": self.last_ai_stats,
            }


class NewsApiHandler(BaseHTTPRequestHandler):
    server_version = "NewsIngestionAPI/1.0"

    @property
    def state(self) -> ApiState:
        return self.server.state  # type: ignore[attr-defined]

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.add_common_headers("application/json")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.handle_request("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.handle_request("POST")

    def handle_request(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        try:
            if path in {"/", "/dashboard"}:
                self.respond_text(load_dashboard_html(), "text/html; charset=utf-8")
            elif path == "/health":
                self.respond_json({"ok": True, "data": self.state.status()})
            elif path == "/api/v1/status":
                self.respond_json({"ok": True, "data": self.state.status()})
            elif path == "/api/v1/dates":
                self.handle_dates()
            elif path == "/api/v1/sources":
                self.handle_sources(params)
            elif path == "/api/v1/articles":
                self.handle_articles(params)
            elif path.startswith("/api/v1/articles/"):
                article_id = unquote(path.rsplit("/", 1)[-1])
                self.handle_article(article_id, params)
            elif path == "/api/v1/hot":
                self.handle_hot(params)
            elif path == "/api/v1/realtime":
                self.handle_realtime(params)
            elif path == "/api/v1/heatmap":
                self.handle_heatmap(params)
            elif path.startswith("/api/v1/sector/") and path.endswith("/news"):
                sector_name = unquote(path.removeprefix("/api/v1/sector/").removesuffix("/news")).strip("/")
                self.handle_sector_news(sector_name, params)
            elif path == "/api/v1/trade-pool":
                self.handle_trade_pool(params)
            elif path == "/api/v1/backtest":
                self.handle_backtest(params)
            elif path == "/api/v1/gpt/status":
                self.handle_gpt_status()
            elif path == "/api/v1/gpt/analyze":
                self.handle_gpt_analyze(params)
            elif path == "/api/v1/gpt/batch-analyze":
                self.handle_gpt_batch_analyze(params)
            elif path == "/api/v1/gpt/analysis":
                self.handle_gpt_analysis(params)
            elif path == "/api/v1/source-health":
                self.handle_source_health(params)
            elif path == "/api/v1/sector-suggestions":
                self.handle_sector_suggestions(params)
            elif path == "/api/v1/stocks":
                self.handle_stocks(params)
            elif path == "/api/v1/events":
                self.handle_events(params)
            elif path == "/api/v1/report":
                self.handle_report(params)
            elif path.startswith("/api/v1/export/"):
                self.handle_export(path, params)
            elif path == "/api/v1/refresh":
                if method != "POST" and not parse_bool(first_param(params, "allow_get"), False):
                    self.respond_error(405, "method_not_allowed", "Use POST /api/v1/refresh.")
                else:
                    self.handle_refresh(params)
            else:
                self.respond_error(404, "not_found", f"Unknown endpoint: {path}")
        except ValueError as exc:
            self.respond_error(400, "bad_request", str(exc))
        except Exception as exc:  # noqa: BLE001
            self.respond_error(500, "internal_error", str(exc))

    def handle_dates(self) -> None:
        data: list[dict[str, Any]] = []
        if self.state.data_root.exists():
            for item in sorted(self.state.data_root.iterdir(), reverse=True):
                if not item.is_dir() or len(item.name) != 8 or not item.name.isdigit():
                    continue
                articles = load_articles(self.state.data_root, item.name)
                report_exists = bool(load_report(self.state.data_root, item.name))
                data.append(
                    {
                        "date": item.name,
                        "date_iso": f"{item.name[:4]}-{item.name[4:6]}-{item.name[6:]}",
                        "article_count": len(articles),
                        "report_exists": report_exists,
                    }
                )
        self.respond_json({"ok": True, "data": data, "meta": {"count": len(data)}})

    def handle_sources(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        records = load_articles(self.state.data_root, date_tag)
        groups: dict[str, dict[str, Any]] = {}
        for record in records:
            key = str(record.get("source_id") or record.get("source") or "unknown")
            group = groups.setdefault(
                key,
                {
                    "source_id": record.get("source_id"),
                    "source": record.get("source"),
                    "source_tier": record.get("source_tier"),
                    "count": 0,
                    "valid": 0,
                    "review": 0,
                    "rejected": 0,
                    "metric_count": 0,
                },
            )
            group["count"] += 1
            status = record.get("status")
            if status in {"valid", "review", "rejected"}:
                group[status] += 1
            engagement = record.get("engagement") or {}
            metrics = record.get("hot_features", {}).get("engagement_metrics", {})
            if engagement.get("has_any_metric") or metrics.get("available_fields"):
                group["metric_count"] += 1
        self.respond_json(
            {
                "ok": True,
                "data": sorted(groups.values(), key=lambda item: item["count"], reverse=True),
                "meta": {"date": date_tag, "date_iso": date_iso, "count": len(groups)},
            }
        )

    def handle_articles(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        include_content = parse_bool(first_param(params, "include_content"), False)
        limit = parse_int(first_param(params, "limit"), 50, minimum=1, maximum=500)
        offset = parse_int(first_param(params, "offset"), 0, minimum=0)
        sort_key = first_param(params, "sort", "newest") or "newest"
        records = filter_records(load_articles(self.state.data_root, date_tag), params)
        records = sort_records(records, sort_key)
        page = records[offset : offset + limit]
        data = [public_article(record, include_content=include_content) for record in page]
        self.respond_json(
            {
                "ok": True,
                "data": data,
                "meta": {
                    "date": date_tag,
                    "date_iso": date_iso,
                    "total": len(records),
                    "limit": limit,
                    "offset": offset,
                    "sort": sort_key,
                },
            }
        )

    def handle_article(self, article_id: str, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        include_content = parse_bool(first_param(params, "include_content"), True)
        for record in load_articles(self.state.data_root, date_tag):
            if str(record.get("article_id") or "") == article_id:
                self.respond_json(
                    {
                        "ok": True,
                        "data": public_article(record, include_content=include_content),
                        "meta": {"date": date_tag, "date_iso": date_iso},
                    }
                )
                return
        self.respond_error(404, "article_not_found", f"Article not found: {article_id}")

    def handle_hot(self, params: dict[str, list[str]]) -> None:
        params = dict(params)
        params["sort"] = ["hot"]
        params.setdefault("status", ["valid"])
        self.handle_articles(params)

    def handle_realtime(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 20, minimum=1, maximum=100)
        sector_dict = load_sector_config()
        ai_map = load_ai_analysis(self.state.data_root, date_tag)
        records = filter_recent(load_articles(self.state.data_root, date_tag), date_tag)  # 过滤旧闻
        records = sort_records(records, "newest")
        records = dedup_articles(records)  # 已按最新排序，去重后保留最新一条
        data: list[dict[str, Any]] = []
        for record in records:
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
        self.respond_json(
            {
                "ok": True,
                "data": data,
                "meta": {"date": date_tag, "date_iso": date_iso, "count": len(data), "limit": limit},
            }
        )

    def handle_heatmap(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 30, minimum=1, maximum=100)
        sector_dict = load_sector_config()
        ai_map = load_ai_analysis(self.state.data_root, date_tag)
        data = build_sector_heatmap(
            load_articles(self.state.data_root, date_tag), sector_dict,
            limit=limit, as_of_date=date_tag, ai_map=ai_map,
        )
        max_heat = max((float(item.get("heat_score") or 0) for item in data), default=0.0)
        self.respond_json(
            {
                "ok": True,
                "data": data,
                "meta": {
                    "date": date_tag,
                    "date_iso": date_iso,
                    "count": len(data),
                    "max_heat_score": round(max_heat, 2),
                    "sector_dictionary": str(DEFAULT_SECTOR_KEYWORDS),
                },
            }
        )

    def handle_sector_news(self, sector_name: str, params: dict[str, list[str]]) -> None:
        if not sector_name:
            self.respond_error(400, "missing_sector", "Sector name is required.")
            return
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 100, minimum=1, maximum=300)
        sector_dict = load_sector_config()
        ai_map = load_ai_analysis(self.state.data_root, date_tag)
        data = filter_sector_articles(
            load_articles(self.state.data_root, date_tag),
            sector_dict,
            sector_name,
            limit=limit,
            as_of_date=date_tag,
            ai_map=ai_map,
        )
        for item in data:
            item["sectors"] = [sector_name]
        self.respond_json(
            {
                "ok": True,
                "data": data,
                "meta": {"date": date_tag, "date_iso": date_iso, "sector": sector_name, "count": len(data)},
            }
        )

    def handle_trade_pool(self, params: dict[str, list[str]]) -> None:
        days = parse_int(first_param(params, "days"), 3, minimum=1, maximum=30)
        limit = parse_int(first_param(params, "limit"), 20, minimum=1, maximum=100)
        sector_dict = load_sector_config()
        date_tags_all = available_date_tags(self.state.data_root)  # ascending
        # "以选中日期为基准往前看 N 天"：只取 <= 选中日期的日期，再取最后 N 个
        as_of = first_param(params, "date")
        if as_of:
            as_of_tag, _ = normalize_date(as_of, self.state.data_root)
            eligible = [d for d in date_tags_all if d <= as_of_tag]
        else:
            eligible = date_tags_all
        selected_dates = eligible[-days:]
        dated_records = [(date_tag, load_articles(self.state.data_root, date_tag)) for date_tag in selected_dates]
        data = build_trade_pool(dated_records, sector_dict, days=days, limit=limit)
        self.respond_json(
            {
                "ok": True,
                "data": data,
                "meta": {"days": days, "dates": selected_dates, "count": len(data), "limit": limit},
            }
        )

    def handle_source_health(self, params: dict[str, list[str]]) -> None:
        """各信息源当日抓取情况 + 报错，用于前端"数据源健康"面板。"""
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        records = load_articles(self.state.data_root, date_tag)
        recent = filter_recent(records, date_tag)
        recent_ids = {id(r) for r in recent}
        groups: dict[str, dict[str, Any]] = {}
        for record in records:
            name = str(record.get("source") or "未知来源")
            g = groups.setdefault(name, {"source": name, "total": 0, "recent": 0})
            g["total"] += 1
            if id(record) in recent_ids:
                g["recent"] += 1
        sources = sorted(groups.values(), key=lambda x: x["recent"], reverse=True)

        # 抓取错误
        errors: list[dict[str, Any]] = []
        err_path = self.state.data_root / date_tag / f"fetch_errors_{date_tag}.jsonl"
        if err_path.exists():
            for line in read_jsonl(err_path):
                errors.append({"source": line.get("source"), "url": line.get("url"), "error": line.get("error")})

        self.respond_json({
            "ok": True,
            "data": {"sources": sources, "errors": errors},
            "meta": {
                "date": date_tag, "date_iso": date_iso,
                "source_count": len(sources), "error_count": len(errors),
                "total_articles": len(records), "recent_articles": len(recent),
            },
        })

    def handle_events(self, params: dict[str, list[str]]) -> None:
        """今日热点事件：把讲同一件事的多源新闻聚成一个事件。"""
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 15, minimum=1, maximum=50)
        sector_dict = load_sector_config()
        ai_map = load_ai_analysis(self.state.data_root, date_tag)
        records = dedup_articles(filter_recent(load_articles(self.state.data_root, date_tag), date_tag))
        events = build_events(records, ai_map, sector_dict, limit=limit)
        self.respond_json({
            "ok": True, "data": events,
            "meta": {"date": date_tag, "date_iso": date_iso, "count": len(events)},
        })

    def handle_stocks(self, params: dict[str, list[str]]) -> None:
        """今日个股提及榜：从 AI 抽取的 stocks 字段聚合，按被提及次数排名。"""
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 30, minimum=1, maximum=100)
        ai_map = load_ai_analysis(self.state.data_root, date_tag)
        records = dedup_articles(filter_recent(load_articles(self.state.data_root, date_tag), date_tag))
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
                group = stocks.setdefault(name, {
                    "name": name, "code": stock.get("code") or "",
                    "count": 0, "importance": 0, "sectors": set(), "news": [],
                })
                if not group["code"] and stock.get("code"):
                    group["code"] = stock["code"]
                group["count"] += 1
                group["importance"] += float(entry.get("importance") or 0)
                for s in entry.get("sectors") or []:
                    group["sectors"].add(s)
                if len(group["news"]) < 10:
                    group["news"].append({
                        "title": compact_text(record.get("title")),
                        "url": record.get("url"),
                        "source": (record.get("source_info") or {}).get("name") or record.get("source"),
                        "published_at": (record.get("time_info") or {}).get("published_at") or record.get("published_at"),
                        "impact": entry.get("impact") or "neutral",
                        "summary": entry.get("summary") or "",
                    })

        ranked = sorted(stocks.values(), key=lambda x: (x["count"], x["importance"]), reverse=True)
        data = []
        for idx, g in enumerate(ranked[:limit], 1):
            data.append({
                "rank": idx, "name": g["name"], "code": g["code"],
                "count": g["count"], "importance": round(g["importance"] / g["count"], 1) if g["count"] else 0,
                "sectors": sorted(g["sectors"]), "news": g["news"],
            })
        self.respond_json({
            "ok": True, "data": data,
            "meta": {"date": date_tag, "date_iso": date_iso, "count": len(data),
                     "ai_analyzed": sum(1 for e in ai_map.values() if e.get("enabled"))},
        })

    def handle_sector_suggestions(self, params: dict[str, list[str]]) -> None:
        """汇总 AI 提出的、不在14类里的新板块建议（按频次），供人工决定是否扩进词典。"""
        days = parse_int(first_param(params, "days"), 7, minimum=1, maximum=30)
        dates = available_date_tags(self.state.data_root)[-days:]
        sector_dict = load_sector_config()
        idx = _alias_index(sector_dict)
        counts: dict[str, int] = {}
        samples: dict[str, list[str]] = {}
        for date_tag in dates:
            ai_map = load_ai_analysis(self.state.data_root, date_tag)
            if not ai_map:
                continue
            id2title = {
                str(r.get("article_id") or ""): str(r.get("title") or "")
                for r in load_articles(self.state.data_root, date_tag)
            }
            for aid, entry in ai_map.items():
                if not entry.get("enabled"):
                    continue
                names: set[str] = set()
                suggested = str(entry.get("suggested_sector") or "").strip()
                if suggested and not idx.get(suggested.lower()):
                    names.add(suggested)
                for raw in entry.get("sectors") or []:  # 兜底：万一界外名混进了 sectors
                    name = str(raw).strip()
                    if name and not idx.get(name.lower()):
                        names.add(name)
                for name in names:
                    counts[name] = counts.get(name, 0) + 1
                    title = id2title.get(str(aid))
                    if title and len(samples.setdefault(name, [])) < 3:
                        samples[name].append(title)
        data = [
            {"name": name, "count": count, "samples": samples.get(name, [])}
            for name, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
        self.respond_json({
            "ok": True,
            "data": data,
            "meta": {"days": days, "dates": dates, "count": len(data)},
        })

    def handle_backtest(self, params: dict[str, list[str]]) -> None:
        top_n = parse_int(first_param(params, "top_n"), 5, minimum=1, maximum=20)
        pool_days = parse_int(first_param(params, "pool_days"), 3, minimum=1, maximum=10)
        sector_dict = load_sector_config()
        date_tags_all = available_date_tags(self.state.data_root)
        dated_records = [(d, load_articles(self.state.data_root, d)) for d in date_tags_all]
        result = run_backtest(dated_records, sector_dict, top_n=top_n, pool_days=pool_days)
        self.respond_json({"ok": True, "data": result, "meta": {"dates": date_tags_all, "top_n": top_n}})

    def handle_gpt_status(self) -> None:
        self.respond_json({"ok": True, "data": provider_info()})

    def handle_gpt_analyze(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        article_id = first_param(params, "article_id")
        if not article_id:
            self.respond_error(400, "missing_article_id", "article_id is required.")
            return
        sector_dict = load_sector_config()
        sector_names = [str(item.get("name")) for item in sector_dict if item.get("name")]
        for record in load_articles(self.state.data_root, date_tag):
            if str(record.get("article_id") or "") != article_id:
                continue
            result = analyze_article_with_gpt(
                str(record.get("title") or ""),
                str(record.get("content") or ""),
                sector_names,
            )
            self.respond_json(
                {
                    "ok": True,
                    "data": result,
                    "meta": {"date": date_tag, "date_iso": date_iso, "article_id": article_id},
                }
            )
            return
        self.respond_error(404, "article_not_found", f"Article not found: {article_id}")

    def handle_gpt_batch_analyze(self, params: dict[str, list[str]]) -> None:
        """Analyze top-N articles for a date and return AI enrichment in bulk.

        Used by the dashboard to decorate the heatmap and news list with
        AI-generated summaries, impact labels, and catalyst notes.
        """
        if not ai_enabled():
            self.respond_json({
                "ok": False,
                "error": {"code": "ai_disabled", "message": "No AI API key configured."},
                "data": [],
            })
            return
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 200, minimum=1, maximum=400)
        sector_dict = load_sector_config()
        records = load_articles(self.state.data_root, date_tag)
        stats = batch_analyze_for_date(records, sector_dict, self.state.data_root, date_tag, max_articles=limit)
        cached = load_ai_analysis(self.state.data_root, date_tag)
        results = [{"article_id": aid, **payload} for aid, payload in cached.items()]
        self.respond_json({
            "ok": True,
            "data": results,
            "meta": {
                "date": date_tag, "date_iso": date_iso,
                "count": len(results), "newly_analyzed": stats.get("analyzed", 0),
                "errors": stats.get("errors", 0), "matched": stats.get("matched", 0),
                **provider_info(),
            },
        })

    def handle_gpt_analysis(self, params: dict[str, list[str]]) -> None:
        """返回某日已持久化的 AI 分析（不触发新的 AI 调用），供前端启动时直接渲染。"""
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        cached = load_ai_analysis(self.state.data_root, date_tag)
        results = [{"article_id": aid, **payload} for aid, payload in cached.items()]
        self.respond_json({
            "ok": True,
            "data": results,
            "meta": {"date": date_tag, "date_iso": date_iso, "count": len(results)},
        })

    def handle_report(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        report = load_report(self.state.data_root, date_tag)
        if first_param(params, "format") == "markdown":
            self.respond_text(report or "report not found", "text/markdown; charset=utf-8", status=200 if report else 404)
            return
        self.respond_json(
            {
                "ok": bool(report),
                "data": {"markdown": report},
                "meta": {"date": date_tag, "date_iso": date_iso},
            },
            status=200 if report else 404,
        )

    def export_records(self, params: dict[str, list[str]], hot_only: bool = False) -> tuple[str, str, list[dict[str, Any]]]:
        date_tag, date_iso = normalize_date(first_param(params, "date"), self.state.data_root)
        limit = parse_int(first_param(params, "limit"), 500, minimum=1, maximum=5000)
        offset = parse_int(first_param(params, "offset"), 0, minimum=0)
        if hot_only:
            params = dict(params)
            params["sort"] = ["hot"]
            params.setdefault("status", ["valid"])
        records = filter_records(load_articles(self.state.data_root, date_tag), params)
        records = sort_records(records, first_param(params, "sort", "newest") or "newest")
        records = records[offset : offset + limit]
        return date_tag, date_iso, records

    def handle_export(self, path: str, params: dict[str, list[str]]) -> None:
        name = path.rsplit("/", 1)[-1]
        if "." not in name:
            self.respond_error(400, "bad_export_path", "Export path must include an extension.")
            return
        target, extension = name.rsplit(".", 1)
        extension = extension.lower()
        include_content = parse_bool(first_param(params, "include_content"), False)
        flattened = parse_bool(first_param(params, "flattened"), False)

        if target == "report" and extension in {"md", "markdown"}:
            date_tag, _ = normalize_date(first_param(params, "date"), self.state.data_root)
            report = load_report(self.state.data_root, date_tag)
            if not report:
                self.respond_error(404, "report_not_found", f"Report not found: {date_tag}")
                return
            self.respond_text(
                report,
                "text/markdown; charset=utf-8",
                filename=date_filename("crawl_report", date_tag, "md"),
            )
            return

        if target == "daily" and extension == "zip":
            self.handle_daily_zip(params)
            return

        if target not in {"articles", "hot"}:
            self.respond_error(400, "bad_export_target", f"Unsupported export target: {target}")
            return
        if extension not in {"csv", "json", "jsonl", "xlsx"}:
            self.respond_error(400, "bad_export_format", f"Unsupported export format: {extension}")
            return

        date_tag, date_iso, records = self.export_records(params, hot_only=target == "hot")
        filename = date_filename(target, date_tag, extension)
        meta = {"date": date_tag, "date_iso": date_iso, "total": len(records), "target": target}
        if extension == "csv":
            body = ("\ufeff" + csv_text(records, include_content=include_content)).encode("utf-8")
            self.respond_bytes(body, "text/csv; charset=utf-8", filename=filename)
        elif extension == "json":
            data = [export_article(record, include_content=include_content) if flattened else public_article(record, include_content=include_content) for record in records]
            self.respond_json({"ok": True, "data": data, "meta": meta}, filename=filename)
        elif extension == "jsonl":
            body = jsonl_text(records, include_content=include_content, flattened=flattened).encode("utf-8")
            self.respond_bytes(body, "application/x-ndjson; charset=utf-8", filename=filename)
        elif extension == "xlsx":
            body = xlsx_bytes(records, include_content=include_content, sheet_name=target)
            self.respond_bytes(body, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filename)

    def handle_daily_zip(self, params: dict[str, list[str]]) -> None:
        date_tag, date_iso, records = self.export_records(params, hot_only=False)
        hot_params = dict(params)
        hot_params["sort"] = ["hot"]
        hot_params.setdefault("status", ["valid"])
        _, _, hot_records = self.export_records(hot_params, hot_only=True)
        report = load_report(self.state.data_root, date_tag)
        metadata = {
            "date": date_tag,
            "date_iso": date_iso,
            "generated_at": now_iso(),
            "article_count": len(records),
            "hot_count": len(hot_records),
            "api_version": API_VERSION,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(date_filename("articles", date_tag, "csv"), "\ufeff" + csv_text(records))
            archive.writestr(date_filename("articles", date_tag, "jsonl"), jsonl_text(records))
            archive.writestr(date_filename("hot", date_tag, "csv"), "\ufeff" + csv_text(hot_records))
            archive.writestr(date_filename("hot", date_tag, "jsonl"), jsonl_text(hot_records))
            archive.writestr(date_filename("articles", date_tag, "xlsx"), xlsx_bytes(records))
            archive.writestr(date_filename("crawl_report", date_tag, "md"), report)
            archive.writestr(date_filename("metadata", date_tag, "json"), json.dumps(metadata, ensure_ascii=False, indent=2))
        self.respond_bytes(
            buffer.getvalue(),
            "application/zip",
            filename=date_filename("news_export_bundle", date_tag, "zip"),
        )

    def handle_refresh(self, params: dict[str, list[str]]) -> None:
        token = os.environ.get("NEWS_API_TOKEN")
        if token:
            auth = self.headers.get("Authorization", "")
            query_token = first_param(params, "token")
            if auth != f"Bearer {token}" and query_token != token:
                self.respond_error(401, "unauthorized", "Missing or invalid NEWS_API_TOKEN.")
                return
        date = first_param(params, "date")
        result = self.state.refresh_once(date=date, reason="manual_api")
        self.respond_json({"ok": True, "data": result, "meta": self.state.status()})

    def respond_json(self, payload: dict[str, Any], status: int = 200, filename: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.add_common_headers("application/json; charset=utf-8")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_text(self, text: str, content_type: str, status: int = 200, filename: str | None = None) -> None:
        body = text.encode("utf-8")
        self.respond_bytes(body, content_type, status=status, filename=filename)

    def respond_bytes(self, body: bytes, content_type: str, status: int = 200, filename: str | None = None) -> None:
        self.send_response(status)
        self.add_common_headers(content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_error(self, status: int, code: str, message: str) -> None:
        self.respond_json({"ok": False, "error": {"code": code, "message": message}}, status=status)

    def add_common_headers(self, content_type: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", os.environ.get("NEWS_API_CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"{timestamp} {self.client_address[0]} {format % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run News Ingestion Team Data API.")
    parser.add_argument("--host", default=os.environ.get("NEWS_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("NEWS_API_PORT", "8080"))))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX_DIR)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--refresh-interval-seconds", type=int, default=None)
    parser.add_argument("--refresh-interval-minutes", type=int, default=None)
    parser.add_argument("--no-refresh-on-start", action="store_true")
    return parser


def resolve_refresh_interval_seconds(args: argparse.Namespace) -> int:
    if args.refresh_interval_seconds is not None:
        return max(0, args.refresh_interval_seconds)
    if args.refresh_interval_minutes is not None:
        return max(0, args.refresh_interval_minutes) * 60

    env_seconds = os.environ.get("NEWS_REFRESH_INTERVAL_SECONDS")
    if env_seconds is not None:
        return parse_int(env_seconds, DEFAULT_REFRESH_INTERVAL_SECONDS, minimum=0)

    env_minutes = os.environ.get("NEWS_REFRESH_INTERVAL_MINUTES")
    if env_minutes is not None:
        return parse_int(env_minutes, 0, minimum=0) * 60

    return DEFAULT_REFRESH_INTERVAL_SECONDS


def main() -> None:
    args = build_parser().parse_args()
    refresh_interval_seconds = resolve_refresh_interval_seconds(args)
    state = ApiState(
        data_root=args.data_root,
        inbox_dir=args.inbox_dir,
        registry_path=args.registry,
        rules_path=args.rules,
        refresh_interval_seconds=refresh_interval_seconds,
        refresh_on_start=not args.no_refresh_on_start,
    )
    state.data_root.mkdir(parents=True, exist_ok=True)
    state.inbox_dir.mkdir(parents=True, exist_ok=True)

    # Check the registry before serving traffic so configuration errors surface early.
    load_registry(args.registry)

    server = ThreadingHTTPServer((args.host, args.port), NewsApiHandler)
    server.state = state  # type: ignore[attr-defined]
    state.start_background_refresh()
    print(
        json.dumps(
            {
                "event": "api_started",
                "host": args.host,
                "port": args.port,
                "refresh_interval_seconds": state.refresh_interval_seconds,
                "refresh_interval_minutes": state.refresh_interval_minutes,
                "refresh_on_start": state.refresh_on_start,
                "started_at": state.started_at,
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
