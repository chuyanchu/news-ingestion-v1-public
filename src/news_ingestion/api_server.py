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
from .io_utils import read_jsonl


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DATA_ROOT = PRODUCT_ROOT / "data" / "daily"
DEFAULT_INBOX_DIR = PRODUCT_ROOT / "data" / "inbox"
API_VERSION = "v1"
EXPORT_METRIC_FIELDS = ["read_count", "view_count", "comment_count", "like_count", "favorite_count", "share_count", "repost_count"]
EXPORT_FIELDNAMES = [
    "article_id",
    "title",
    "source",
    "source_id",
    "source_tier",
    "published_at",
    "crawled_at",
    "author",
    "section",
    "status",
    "quality_score",
    "quality_flags",
    "hot_rank_score",
    "list_rank",
    "list_size",
    "source_priority",
    "engagement_available_fields",
    "read_count",
    "view_count",
    "comment_count",
    "like_count",
    "favorite_count",
    "share_count",
    "repost_count",
    "url",
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
        "hot_rank_score": hot_rank_score(record),
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
    hot_features = record.get("hot_features") or {}
    prominence = hot_features.get("source_prominence") or {}
    metrics = hot_features.get("engagement_metrics") or {}
    item = {
        "article_id": record.get("article_id"),
        "title": compact_text(record.get("title")),
        "source": compact_text(record.get("source")),
        "source_id": record.get("source_id"),
        "source_tier": record.get("source_tier"),
        "published_at": record.get("published_at"),
        "crawled_at": record.get("crawled_at"),
        "author": compact_text(record.get("author")),
        "section": compact_text(record.get("section")),
        "status": record.get("status"),
        "quality_score": record.get("quality_score"),
        "quality_flags": ";".join(record.get("quality_flags") or []),
        "hot_rank_score": hot_rank_score(record),
        "list_rank": prominence.get("list_rank"),
        "list_size": prominence.get("list_size"),
        "source_priority": prominence.get("source_priority"),
        "engagement_available_fields": ";".join(metrics.get("available_fields") or []),
        "url": record.get("url"),
        "content_excerpt": compact_text(content, 300),
    }
    for field in EXPORT_METRIC_FIELDS:
        item[field] = metrics.get(field)
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
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


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
        refresh_interval_minutes: int,
        refresh_on_start: bool,
    ) -> None:
        self.data_root = data_root
        self.inbox_dir = inbox_dir
        self.registry_path = registry_path
        self.rules_path = rules_path
        self.refresh_interval_minutes = refresh_interval_minutes
        self.refresh_on_start = refresh_on_start
        self.lock = threading.RLock()
        self.started_at = now_iso()
        self.last_refresh_started_at: str | None = None
        self.last_refresh_completed_at: str | None = None
        self.last_refresh_error: str | None = None
        self.refresh_count = 0
        self.running_refresh = False
        self.stop_event = threading.Event()

    def start_background_refresh(self) -> None:
        if not self.refresh_on_start and self.refresh_interval_minutes <= 0:
            return
        thread = threading.Thread(target=self._background_loop, name="news-api-refresh", daemon=True)
        thread.start()

    def _background_loop(self) -> None:
        if self.refresh_on_start:
            self.refresh_once(reason="startup")
        while self.refresh_interval_minutes > 0 and not self.stop_event.wait(self.refresh_interval_minutes * 60):
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
                return {
                    "reason": reason,
                    "date": date_tags(date)[0] if date else date_tags(None)[0],
                    "completed_at": self.last_refresh_completed_at,
                    "refresh_count": self.refresh_count,
                }
            except Exception as exc:  # noqa: BLE001
                self.last_refresh_error = str(exc)
                raise
            finally:
                self.running_refresh = False

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "api_version": API_VERSION,
                "started_at": self.started_at,
                "data_root": str(self.data_root),
                "latest_date": latest_date_tag(self.data_root),
                "refresh_interval_minutes": self.refresh_interval_minutes,
                "refresh_on_start": self.refresh_on_start,
                "running_refresh": self.running_refresh,
                "last_refresh_started_at": self.last_refresh_started_at,
                "last_refresh_completed_at": self.last_refresh_completed_at,
                "last_refresh_error": self.last_refresh_error,
                "refresh_count": self.refresh_count,
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
            if path == "/health":
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
            metrics = record.get("hot_features", {}).get("engagement_metrics", {})
            if metrics.get("available_fields"):
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
    parser.add_argument("--refresh-interval-minutes", type=int, default=int(os.environ.get("NEWS_REFRESH_INTERVAL_MINUTES", "10")))
    parser.add_argument("--no-refresh-on-start", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    state = ApiState(
        data_root=args.data_root,
        inbox_dir=args.inbox_dir,
        registry_path=args.registry,
        rules_path=args.rules,
        refresh_interval_minutes=max(0, args.refresh_interval_minutes),
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
