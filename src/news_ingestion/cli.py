from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .fetchers import fetch_registry_html, fetch_registry_rss
from .io_utils import read_json, read_jsonl, write_jsonl, write_text
from .quality import DEFAULT_RULES, evaluate_record
from .report import build_report


PRODUCT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PRODUCT_ROOT / "config" / "source_registry.v1.json"
DEFAULT_RULES_PATH = PRODUCT_ROOT / "config" / "quality_rules.v1.json"
DEFAULT_SAMPLE = PRODUCT_ROOT / "templates" / "articles_input.sample.jsonl"
CN_TZ = ZoneInfo("Asia/Shanghai")


def load_registry(path: Path | None) -> dict:
    return read_json(path or DEFAULT_REGISTRY)


def load_rules(path: Path | None) -> dict:
    if path and path.exists():
        return read_json(path)
    if DEFAULT_RULES_PATH.exists():
        return read_json(DEFAULT_RULES_PATH)
    return DEFAULT_RULES


def date_tags(value: str | None = None) -> tuple[str, str]:
    if value:
        normalized = value.replace("-", "")
        if len(normalized) != 8 or not normalized.isdigit():
            raise ValueError("--date must be YYYYMMDD or YYYY-MM-DD")
        return normalized, f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    now = datetime.now(CN_TZ)
    return now.strftime("%Y%m%d"), now.strftime("%Y-%m-%d")


def validate_loaded_records(
    records: list[dict],
    out_dir: Path,
    registry: dict,
    rules: dict,
    date_tag: str | None = None,
    report_date: str | None = None,
) -> tuple[list[dict], list[dict]]:
    date_tag = date_tag or datetime.now(CN_TZ).strftime("%Y%m%d")
    report_date = report_date or f"{date_tag[:4]}-{date_tag[4:6]}-{date_tag[6:]}"
    evaluated = [evaluate_record(record, registry=registry, rules=rules) for record in records]
    accepted = [record for record in evaluated if record.get("status") in {"valid", "review"}]
    rejected = [record for record in evaluated if record.get("status") == "rejected"]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "articles_valid.jsonl", accepted)
    write_jsonl(out_dir / "articles_rejected.jsonl", rejected)
    write_jsonl(out_dir / f"articles_{date_tag}.jsonl", accepted)
    write_jsonl(out_dir / f"rejected_{date_tag}.jsonl", rejected)
    report = build_report(accepted, rejected, registry=registry, report_date=report_date)
    write_text(out_dir / "crawl_report.md", report)
    write_text(out_dir / f"crawl_report_{date_tag}.md", report)
    return accepted, rejected


def validate_records(input_path: Path, out_dir: Path, registry_path: Path | None = None, rules_path: Path | None = None, date: str | None = None) -> None:
    registry = load_registry(registry_path)
    rules = load_rules(rules_path)
    date_tag, report_date = date_tags(date)
    input_records = read_jsonl(input_path)
    accepted, rejected = validate_loaded_records(input_records, out_dir, registry, rules, date_tag, report_date)
    print(f"validated={len(input_records)} valid_or_review={len(accepted)} rejected={len(rejected)}")
    print(f"out_dir={out_dir}")


def create_sample(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = out_dir / "articles_input.sample.jsonl"
    shutil.copyfile(DEFAULT_SAMPLE, input_path)
    validate_records(input_path=input_path, out_dir=out_dir)


def daily_collect(
    out_root: Path,
    inbox_dir: Path,
    registry_path: Path | None = None,
    rules_path: Path | None = None,
    date: str | None = None,
    fetch_rss: bool = True,
    fetch_html: bool = True,
    analyze: bool = False,
) -> None:
    registry = load_registry(registry_path)
    rules = load_rules(rules_path)
    date_tag, report_date = date_tags(date)
    out_dir = out_root / date_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        inbox_dir / f"articles_{date_tag}.input.jsonl",
        inbox_dir / f"articles_input_{date_tag}.jsonl",
        inbox_dir / "articles_input.jsonl",
    ]
    records: list[dict] = []
    used_inputs: list[str] = []
    for candidate in candidates:
        if candidate.exists():
            loaded = read_jsonl(candidate)
            records.extend(loaded)
            used_inputs.append(str(candidate))

    rss_errors: list[dict] = []
    if fetch_rss:
        rss_records, rss_errors = fetch_registry_rss(registry)
        records.extend(rss_records)
    html_errors: list[dict] = []
    if fetch_html:
        html_records, html_errors = fetch_registry_html(registry)
        records.extend(html_records)

    fetch_errors = rss_errors + html_errors
    write_jsonl(out_dir / f"articles_input_{date_tag}.jsonl", records)
    if fetch_errors:
        write_jsonl(out_dir / f"fetch_errors_{date_tag}.jsonl", fetch_errors)
    accepted, rejected = validate_loaded_records(records, out_dir, registry, rules, date_tag, report_date)

    # 按真实发布日期重新归档：把抓到的跨多天新闻分配到各自的日期文件夹。
    # 先把运行当天文件夹收敛成"只含当天发布"（含无日期的兜底放当天），再分发全部 accepted。
    from .rebucket import article_date_tag, rebucket_by_date

    today_only = [r for r in accepted if (article_date_tag(r) or date_tag) == date_tag]
    write_jsonl(out_dir / f"articles_{date_tag}.jsonl", today_only)
    bucketed = rebucket_by_date(accepted, out_root, as_of_tag=date_tag)

    metadata = {
        "date": report_date,
        "date_tag": date_tag,
        "input_files": used_inputs,
        "fetched_rss_errors": len(rss_errors),
        "fetched_html_errors": len(html_errors),
        "input_records": len(records),
        "accepted_records": len(accepted),
        "rejected_records": len(rejected),
        "bucketed_dates": bucketed,
        "output_dir": str(out_dir),
    }
    write_jsonl(out_dir / f"run_metadata_{date_tag}.jsonl", [metadata])
    print(f"daily_date={date_tag} input={len(records)} valid_or_review={len(accepted)} rejected={len(rejected)}")
    print(f"rebucketed_dates={bucketed}")

    if analyze:
        from .gpt_analyzer import ai_enabled, batch_analyze_for_date
        from .sector_heat import load_sector_dictionary
        if ai_enabled():
            sector_dict = load_sector_dictionary(PRODUCT_ROOT / "config" / "sector_keywords.v1.json")
            stats = batch_analyze_for_date(today_only, sector_dict, out_root, date_tag)
            print(f"ai_analyze={stats}")
        else:
            print("ai_analyze=skipped (no API key)")

    print(f"out_dir={out_dir}")


def create_report(articles_path: Path, rejected_path: Path, out_path: Path, registry_path: Path | None = None) -> None:
    registry = load_registry(registry_path)
    articles = read_jsonl(articles_path)
    rejected = read_jsonl(rejected_path)
    report = build_report(articles, rejected, registry=registry, report_date=datetime.now().strftime("%Y-%m-%d"))
    write_text(out_path, report)
    print(f"report={out_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="News Ingestion V1 toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sample = subparsers.add_parser("sample", help="Generate sample ArticleRecord outputs and report")
    sample.add_argument("--out-dir", type=Path, default=PRODUCT_ROOT / "data" / "samples")

    validate = subparsers.add_parser("validate", help="Validate JSONL records and split accepted/rejected outputs")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--out-dir", type=Path, required=True)
    validate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    validate.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    validate.add_argument("--date", type=str, default=None)

    daily = subparsers.add_parser("daily", help="Run the daily ingestion workflow")
    daily.add_argument("--out-root", type=Path, default=PRODUCT_ROOT / "data" / "daily")
    daily.add_argument("--inbox-dir", type=Path, default=PRODUCT_ROOT / "data" / "inbox")
    daily.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    daily.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    daily.add_argument("--date", type=str, default=None)
    daily.add_argument("--no-rss", action="store_true", help="Skip RSS entrypoints in source registry")
    daily.add_argument("--no-html", action="store_true", help="Skip HTML entrypoints in source registry")
    daily.add_argument("--analyze", action="store_true", help="采集后对命中板块的新闻自动跑 AI 分析（需配置 API key）")

    report = subparsers.add_parser("report", help="Generate crawl health report")
    report.add_argument("--articles", type=Path, required=True)
    report.add_argument("--rejected", type=Path, required=True)
    report.add_argument("--out", type=Path, required=True)
    report.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "sample":
        create_sample(args.out_dir)
    elif args.command == "validate":
        validate_records(args.input, args.out_dir, args.registry, args.rules, args.date)
    elif args.command == "daily":
        daily_collect(
            args.out_root,
            args.inbox_dir,
            args.registry,
            args.rules,
            args.date,
            fetch_rss=not args.no_rss,
            fetch_html=not args.no_html,
            analyze=args.analyze,
        )
    elif args.command == "report":
        create_report(args.articles, args.rejected, args.out, args.registry)
    else:
        parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
