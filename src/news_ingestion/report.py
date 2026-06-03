from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Any


def _avg_quality(records: list[dict[str, Any]]) -> str:
    values = [float(record.get("quality_score", 0)) for record in records if record.get("quality_score") is not None]
    if not values:
        return "0.000"
    return f"{mean(values):.3f}"


def _metrics_available_count(records: list[dict[str, Any]]) -> int:
    count = 0
    for record in records:
        metrics = record.get("hot_features", {}).get("engagement_metrics", {})
        if metrics.get("available_fields"):
            count += 1
    return count


def _prominence_available_count(records: list[dict[str, Any]]) -> int:
    count = 0
    for record in records:
        prominence = record.get("hot_features", {}).get("source_prominence", {})
        if prominence.get("list_rank") is not None:
            count += 1
    return count


def _metric_field_counts(records: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in records:
        metrics = record.get("hot_features", {}).get("engagement_metrics", {})
        for field in metrics.get("available_fields") or []:
            counts[field] += 1
    return counts


def build_report(
    articles: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    registry: dict[str, Any] | None = None,
    report_date: str | None = None,
) -> str:
    registry = registry or {}
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")
    all_records = articles + rejected
    status_counts = Counter(record.get("status", "unknown") for record in all_records)
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flag_counts: Counter[str] = Counter()
    for record in all_records:
        source_groups[str(record.get("source") or "UNKNOWN")].append(record)
        flag_counts.update(record.get("quality_flags") or [])

    source_tiers = {}
    for source in registry.get("sources", []):
        if source.get("name"):
            source_tiers[source["name"]] = source.get("tier", "UNKNOWN")
        if source.get("source_id"):
            source_tiers[source["source_id"]] = source.get("tier", "UNKNOWN")

    lines: list[str] = []
    lines.append("# 每日采集健康报告")
    lines.append("")
    lines.append(f"日期：`{report_date}`")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 输入新闻数 | {len(all_records)} |")
    lines.append(f"| 有效新闻数 | {status_counts.get('valid', 0)} |")
    lines.append(f"| 待复核新闻数 | {status_counts.get('review', 0)} |")
    lines.append(f"| 拒收新闻数 | {status_counts.get('rejected', 0)} |")
    lines.append(f"| 数据源数量 | {len(source_groups)} |")
    lines.append(f"| 平均质量分 | {_avg_quality(all_records)} |")
    lines.append(f"| 列表排名覆盖 | {_prominence_available_count(all_records)}/{len(all_records)} |")
    lines.append(f"| 互动指标覆盖 | {_metrics_available_count(all_records)}/{len(all_records)} |")
    lines.append("")
    lines.append("## 数据源表现")
    lines.append("")
    lines.append("| source | tier | valid | review | rejected | avg_quality | rank_coverage | metric_coverage | top_flags |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for source, records in sorted(source_groups.items()):
        counts = Counter(record.get("status", "unknown") for record in records)
        flags = Counter()
        for record in records:
            flags.update(record.get("quality_flags") or [])
        top_flags = ", ".join(flag for flag, _ in flags.most_common(5)) or "-"
        tier = source_tiers.get(source, records[0].get("source_tier", "UNKNOWN"))
        lines.append(
            f"| {source} | {tier} | {counts.get('valid', 0)} | {counts.get('review', 0)} | "
            f"{counts.get('rejected', 0)} | {_avg_quality(records)} | "
            f"{_prominence_available_count(records)}/{len(records)} | {_metrics_available_count(records)}/{len(records)} | {top_flags} |"
        )
    lines.append("")
    lines.append("## 热点指标覆盖")
    lines.append("")
    lines.append("| metric | records_with_metric |")
    lines.append("| --- | --- |")
    metric_counts = _metric_field_counts(all_records)
    if metric_counts:
        for metric, count in metric_counts.most_common():
            lines.append(f"| {metric} | {count} |")
    else:
        lines.append("| - | 0 |")
    lines.append("")
    lines.append("## 质量问题")
    lines.append("")
    lines.append("| flag | count | 处理建议 |")
    lines.append("| --- | --- | --- |")
    if flag_counts:
        for flag, count in flag_counts.most_common():
            lines.append(f"| {flag} | {count} | 查看对应源适配器或人工复核。 |")
    else:
        lines.append("| - | 0 | 今日无明显质量问题。 |")
    lines.append("")
    lines.append("## 今日输出文件")
    lines.append("")
    lines.append(f"- `articles_{report_date.replace('-', '')}.jsonl`")
    lines.append(f"- `rejected_{report_date.replace('-', '')}.jsonl`")
    lines.append(f"- `crawl_report_{report_date.replace('-', '')}.md`")
    lines.append("")
    lines.append("## 人工复核清单")
    lines.append("")
    lines.append("- 时间戳缺失或异常的新闻。")
    lines.append("- 正文过短但标题疑似重要的新闻。")
    lines.append("- 新增数据源中质量分低于 0.75 的新闻。")
    lines.append("- T2 社区源中可能引发舆情但证据不足的文本。")
    lines.append("")
    return "\n".join(lines)
