from __future__ import annotations

import re
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def to_iso_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ).isoformat()


def parse_local_datetime(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y年%m月%d日 %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=CN_TZ).isoformat()
        except ValueError:
            continue
    return None


def fetch_url(url: str, timeout: int = 15) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "QuantNewsAgent/1.0 (+local research project)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _text(element: ET.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def parse_rss_or_atom(xml_bytes: bytes, source: dict[str, Any], feed_url: str, crawled_at: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    records: list[dict[str, Any]] = []

    items = root.findall(".//item")
    for item in items:
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        description = _text(item.find("description"))
        pub_date = _text(item.find("pubDate")) or _text(item.find("date"))
        records.append(
            {
                "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                "source_id": source.get("source_id"),
                "title": title,
                "content": strip_html(description),
                "url": link,
                "published_at": to_iso_datetime(pub_date),
                "crawled_at": crawled_at,
                "section": "rss",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": feed_url,
            }
        )

    if records:
        return records

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//atom:entry", ns) or root.findall(".//entry")
    for entry in entries:
        title = _text(entry.find("atom:title", ns) or entry.find("title"))
        summary = _text(entry.find("atom:summary", ns) or entry.find("summary"))
        updated = _text(entry.find("atom:updated", ns) or entry.find("updated"))
        link = ""
        for link_node in entry.findall("atom:link", ns) + entry.findall("link"):
            href = link_node.attrib.get("href")
            if href:
                link = href
                break
        records.append(
            {
                "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                "source_id": source.get("source_id"),
                "title": title,
                "content": strip_html(summary),
                "url": link,
                "published_at": updated,
                "crawled_at": crawled_at,
                "section": "atom",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": feed_url,
            }
        )

    return records


def decode_html(payload: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def meta_content(html: str, key: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(key)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return unescape(match.group(1).strip())
    return None


def clean_html_text(fragment: str) -> str:
    fragment = re.sub(r"<script\b[^>]*>.*?</script>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b[^>]*>.*?</style>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<!--.*?-->", " ", fragment, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    text = text.split("责任编辑：", 1)[0].strip()
    text = text.split(".appendQr_wrap", 1)[0].strip()
    return text


METRIC_FIELDS = ["read_count", "view_count", "comment_count", "like_count", "favorite_count", "share_count", "repost_count"]


def parse_count(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = value.replace(",", "").strip()
    multiplier = 1
    if cleaned.endswith("万"):
        multiplier = 10000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("亿"):
        multiplier = 100000000
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return None


def find_metric_by_labels(text: str, labels: list[str]) -> int | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"(?:{label_pattern})\s*(?:数|量|次数)?\s*[:：]?\s*([0-9][0-9,.]*(?:万|亿)?)",
        rf"([0-9][0-9,.]*(?:万|亿)?)\s*(?:次)?\s*(?:{label_pattern})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            parsed = parse_count(match.group(1))
            if parsed is not None:
                return parsed
    return None


def find_metric_by_keys(html: str, keys: list[str]) -> int | None:
    key_pattern = "|".join(re.escape(key) for key in keys)
    patterns = [
        rf'["\'](?:{key_pattern})["\']\s*:\s*["\']?([0-9][0-9,.]*)',
        rf'(?:{key_pattern})\s*=\s*["\']([0-9][0-9,.]*)["\']',
        rf'data-(?:{key_pattern.replace("_", "-")})\s*=\s*["\']([0-9][0-9,.]*)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I)
        if match:
            parsed = parse_count(match.group(1))
            if parsed is not None:
                return parsed
    return None


def extract_engagement_metrics(html: str | None, collected_at: str, metric_source: str, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    html = html or ""
    text = clean_html_text(html)
    metric_specs = {
        "read_count": (["阅读", "阅读数", "阅读量"], ["read_count", "readCount", "readnum", "readNum"]),
        "view_count": (["浏览", "浏览数", "浏览量", "播放"], ["view_count", "viewCount", "viewnum", "pv", "playCount"]),
        "comment_count": (["评论", "评论数", "评论量"], ["comment_count", "commentCount", "commentnum", "commentNum"]),
        "like_count": (["点赞", "赞"], ["like_count", "likeCount", "like_num", "likeNum", "praise"]),
        "favorite_count": (["收藏", "收藏数"], ["favorite_count", "favoriteCount", "fav_count", "favCount", "collect_count", "collectCount"]),
        "share_count": (["分享", "分享数"], ["share_count", "shareCount", "share_num", "shareNum"]),
        "repost_count": (["转发", "转载", "转发数"], ["repost_count", "repostCount", "forward_count", "forwardCount"]),
    }
    metrics: dict[str, Any] = {
        field: None for field in METRIC_FIELDS
    }
    metrics.update(
        {
            "collected_at": collected_at,
            "source": metric_source,
            "available_fields": [],
            "missing_fields": [],
            "quality_flags": [],
            "raw": raw or {},
        }
    )
    for field, (labels, keys) in metric_specs.items():
        value = find_metric_by_keys(html, keys)
        if value is None:
            value = find_metric_by_labels(text, labels)
        metrics[field] = value
        if value is None:
            metrics["missing_fields"].append(field)
        else:
            metrics["available_fields"].append(field)
    if not metrics["available_fields"]:
        metrics["quality_flags"].append("no_engagement_metrics_found")
    return metrics


def merge_metric(metrics: dict[str, Any], field: str, value: int | None, raw_key: str, raw_value: Any) -> dict[str, Any]:
    if value is None:
        return metrics
    metrics[field] = value
    if field not in metrics["available_fields"]:
        metrics["available_fields"].append(field)
    metrics["missing_fields"] = [missing for missing in metrics["missing_fields"] if missing != field]
    metrics["raw"][raw_key] = raw_value
    metrics["quality_flags"] = [flag for flag in metrics["quality_flags"] if flag != "no_engagement_metrics_found"]
    return metrics


def build_hot_features(
    source: dict[str, Any],
    entrypoint_url: str,
    crawled_at: str,
    list_rank: int | None,
    list_size: int | None,
    engagement_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_prominence": {
            "list_rank": list_rank,
            "list_size": list_size,
            "source_priority": source.get("priority"),
            "source_tier": source.get("tier"),
            "entrypoint": entrypoint_url,
            "captured_at": crawled_at,
        },
        "engagement_metrics": engagement_metrics
        or extract_engagement_metrics(None, crawled_at, "not_available"),
    }


def extract_sina_article(html: str, fallback_title: str) -> tuple[str, str | None, str | None]:
    published_at = (
        meta_content(html, "article:published_time")
        or meta_content(html, "bytedance:published_time")
        or meta_content(html, "bytedance:updated_time")
    )
    author = meta_content(html, "article:author")
    content = meta_content(html, "description") or ""
    body_match = re.search(r'<div[^>]+id=["\']artibody["\'][^>]*>(.*?)<div[^>]+class=["\']artical-player-wrap', html, flags=re.I | re.S)
    if not body_match:
        body_match = re.search(r'<div[^>]+id=["\']artibody["\'][^>]*>(.*?)</div>', html, flags=re.I | re.S)
    if body_match:
        body_text = clean_html_text(body_match.group(1))
        if len(body_text) > len(content):
            content = body_text
    return content or fallback_title, published_at, author


def extract_sina_comment_identity(html: str) -> tuple[str | None, str | None]:
    comment_meta = meta_content(html, "comment")
    if comment_meta and ":" in comment_meta:
        channel, newsid = comment_meta.split(":", 1)
        return channel.strip(), newsid.strip()
    sudameta_matches = re.findall(r'<meta[^>]+name=["\']sudameta["\'][^>]+content=["\']([^"\']+)["\']', html, flags=re.I)
    for content in sudameta_matches:
        channel_match = re.search(r"comment_channel:([^;]+)", content)
        newsid_match = re.search(r"comment_id:([^;]+)", content)
        if channel_match and newsid_match:
            return channel_match.group(1).strip(), newsid_match.group(1).strip()
    channel_match = re.search(r"channel:\s*['\"]([^'\"]+)['\"]", html)
    newsid_match = re.search(r"newsid:\s*['\"]([^'\"]+)['\"]", html)
    if channel_match and newsid_match:
        return channel_match.group(1).strip(), newsid_match.group(1).strip()
    return None, None


def fetch_sina_comment_metrics(channel: str | None, newsid: str | None, timeout: int, collected_at: str) -> dict[str, Any]:
    metrics = extract_engagement_metrics(None, collected_at, "sina_comment_api")
    if not channel or not newsid:
        metrics["quality_flags"].append("missing_sina_comment_identity")
        return metrics
    api_url = f"https://comment5.news.sina.com.cn/cmnt/count?format=json&newslist={urllib.parse.quote(channel + ':' + newsid)}"
    try:
        payload = fetch_url(api_url, timeout=timeout)
        obj = json.loads(payload.decode("utf-8", errors="replace"))
        count_obj = obj.get("result", {}).get("count", {}).get(f"{channel}:{newsid}", {})
        total = count_obj.get("total")
        if total is not None:
            metrics = merge_metric(metrics, "comment_count", int(total), "sina_comment_count", count_obj)
            metrics["source"] = "sina_comment_api"
            metrics["raw"]["api_url"] = api_url
            metrics["raw"]["comment_channel"] = channel
            metrics["raw"]["comment_id"] = newsid
    except Exception as exc:  # noqa: BLE001
        metrics["quality_flags"].append("sina_comment_api_error")
        metrics["raw"]["sina_comment_api_error"] = str(exc)
    return metrics


def extract_between(html: str, start_pattern: str, end_patterns: list[str]) -> str:
    start = re.search(start_pattern, html, flags=re.I | re.S)
    if not start:
        return ""
    start_idx = start.end()
    end_candidates = [html.find(marker, start_idx) for marker in end_patterns]
    end_candidates = [idx for idx in end_candidates if idx != -1]
    end_idx = min(end_candidates) if end_candidates else len(html)
    return html[start_idx:end_idx]


def resolve_url(base: str, href: str) -> str:
    if href.startswith("//"):
        return "https:" + href
    return urllib.parse.urljoin(base, href)


def normalize_sina_link(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if "cj.sina.cn/article/norm_detail" in href:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        return query.get("url", [href])[0]
    return href


def date_from_url(url: str) -> str | None:
    match = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", url)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T00:00:00+08:00"


def parse_sina_roll_list(html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
        href = normalize_sina_link(match.group(1).strip())
        title = clean_html_text(match.group(2))
        if not title or len(title) < 6:
            continue
        if "finance.sina.com.cn" not in href or "doc-" not in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append((title, href))
    return links


def fetch_sina_roll(source: dict[str, Any], entrypoint: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = entrypoint["url"]
    max_items = int(entrypoint.get("max_items", 30))
    fetch_detail = bool(entrypoint.get("fetch_detail", True))
    crawled_at = now_cn().isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    list_payload = fetch_url(url, timeout=timeout)
    list_html = decode_html(list_payload)
    links = parse_sina_roll_list(list_html)[:max_items]
    list_size = len(links)

    for list_rank, (title, article_url) in enumerate(links, 1):
        content = title
        published_at = date_from_url(article_url)
        author = None
        engagement_metrics = extract_engagement_metrics(None, crawled_at, "not_available")
        if fetch_detail:
            try:
                detail_payload = fetch_url(article_url, timeout=timeout)
                detail_html = decode_html(detail_payload)
                content, detail_published_at, author = extract_sina_article(detail_html, title)
                published_at = detail_published_at or published_at
                engagement_metrics = extract_engagement_metrics(detail_html, crawled_at, "sina_finance_detail_html")
                comment_channel, comment_id = extract_sina_comment_identity(detail_html)
                comment_metrics = fetch_sina_comment_metrics(comment_channel, comment_id, timeout, crawled_at)
                if comment_metrics.get("comment_count") is not None:
                    engagement_metrics = merge_metric(
                        engagement_metrics,
                        "comment_count",
                        comment_metrics.get("comment_count"),
                        "sina_comment_api",
                        comment_metrics.get("raw", {}),
                    )
                    engagement_metrics["source"] = "sina_finance_detail_html+sina_comment_api"
                else:
                    engagement_metrics["raw"]["sina_comment_api"] = comment_metrics.get("raw", {})
                    engagement_metrics["quality_flags"].extend(comment_metrics.get("quality_flags") or [])
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                        "source_id": source.get("source_id"),
                        "url": article_url,
                        "error": str(exc),
                        "crawled_at": crawled_at,
                    }
                )
        records.append(
            {
                "source": source.get("name") or "新浪财经",
                "source_id": source.get("source_id") or "sina_finance",
                "title": title,
                "content": content,
                "url": article_url,
                "published_at": published_at,
                "crawled_at": crawled_at,
                "author": author,
                "section": "滚动新闻",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": url,
                "hot_features": build_hot_features(source, url, crawled_at, list_rank, list_size, engagement_metrics),
            }
        )

    return records, errors


def fetch_eastmoney_api(source: dict[str, Any], entrypoint: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = entrypoint["url"]
    max_items = int(entrypoint.get("max_items", 20))
    crawled_at = now_cn().isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        payload = fetch_url(url, timeout=timeout)
        obj = json.loads(payload.decode("utf-8", errors="replace"))
        items = obj.get("data", {}).get("list", [])[:max_items]
        list_size = len(items)
        for list_rank, item in enumerate(items, 1):
            raw_metrics = {
                key: item.get(key)
                for key in ("commentCount", "readCount", "likeCount", "shareCount", "collectCount")
                if item.get(key) is not None
            }
            # Use the real publisher name from the API response when available
            real_media = str(item.get("mediaName") or "").strip()
            display_source = real_media if real_media else (source.get("name") or "东方财富")
            records.append(
                {
                    "source": display_source,
                    "source_id": source.get("source_id") or "eastmoney",
                    "title": item.get("title") or "",
                    "content": item.get("summary") or item.get("title") or "",
                    "url": item.get("uniqueUrl") or item.get("url") or "",
                    "published_at": parse_local_datetime(item.get("showTime")),
                    "crawled_at": crawled_at,
                    "author": real_media,
                    "section": entrypoint.get("label") or "新闻API",
                    "keywords": [],
                    "raw_html_path": None,
                    "fetch_entrypoint": url,
                    "hot_features": build_hot_features(
                        source,
                        url,
                        crawled_at,
                        list_rank,
                        list_size,
                        extract_engagement_metrics(json.dumps(item, ensure_ascii=False), crawled_at, "eastmoney_api_payload", raw_metrics),
                    ),
                }
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(
            {
                "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                "source_id": source.get("source_id"),
                "url": url,
                "error": str(exc),
                "crawled_at": crawled_at,
            }
        )
    return records, errors


def parse_stcn_list(html: str, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']*/article/detail/\d+\.html)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
        title = clean_html_text(match.group(2))
        url = resolve_url(base_url, match.group(1).strip())
        if len(title) < 6 or url in seen:
            continue
        seen.add(url)
        links.append((title, url))
    return links


def extract_stcn_article(html: str, fallback_title: str) -> tuple[str, str | None, str | None]:
    title_match = re.search(r'<div[^>]+class=["\']detail-title["\'][^>]*>(.*?)</div>', html, flags=re.I | re.S)
    title = clean_html_text(title_match.group(1)) if title_match else fallback_title
    info_match = re.search(r'<div[^>]+class=["\']detail-info["\'][^>]*>(.*?)</div>', html, flags=re.I | re.S)
    published_at = None
    author = None
    if info_match:
        info_text = clean_html_text(info_match.group(1))
        date_match = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", info_text)
        if date_match:
            published_at = parse_local_datetime(date_match.group(0))
        source_match = re.search(r"来源：?([^\s]+)", info_text)
        if source_match:
            author = source_match.group(1)
    fragment = extract_between(
        html,
        r'<div[^>]+class=["\']detail-content["\'][^>]*>',
        ['<div class="statement"', '<div class="detail-statement"', '<div id="comment"', '<div class="recommend"'],
    )
    content = clean_html_text(fragment)
    return content or title, published_at, author


def fetch_stcn_articles(source: dict[str, Any], entrypoint: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = entrypoint["url"]
    max_items = int(entrypoint.get("max_items", 20))
    fetch_detail = bool(entrypoint.get("fetch_detail", True))
    crawled_at = now_cn().isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    list_html = decode_html(fetch_url(url, timeout=timeout))
    links = parse_stcn_list(list_html, url)[:max_items]
    list_size = len(links)
    for list_rank, (title, article_url) in enumerate(links, 1):
        content = title
        published_at = None
        author = None
        engagement_metrics = extract_engagement_metrics(None, crawled_at, "not_available")
        if fetch_detail:
            try:
                detail_html = decode_html(fetch_url(article_url, timeout=timeout))
                content, published_at, author = extract_stcn_article(detail_html, title)
                engagement_metrics = extract_engagement_metrics(detail_html, crawled_at, "stcn_detail_html")
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": source.get("name"), "source_id": source.get("source_id"), "url": article_url, "error": str(exc), "crawled_at": crawled_at})
        records.append(
            {
                "source": source.get("name") or "证券时报",
                "source_id": source.get("source_id") or "stcn",
                "title": title,
                "content": content,
                "url": article_url,
                "published_at": published_at or date_from_url(article_url),
                "crawled_at": crawled_at,
                "author": author,
                "section": "文章列表",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": url,
                "hot_features": build_hot_features(source, url, crawled_at, list_rank, list_size, engagement_metrics),
            }
        )
    return records, errors


def parse_cctv_home(html: str, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']*finance\.cctv\.com/\d{4}/\d{2}/\d{2}/[^"\']+\.shtml)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
        title = clean_html_text(match.group(2))
        url = resolve_url(base_url, match.group(1).strip())
        if len(title) < 6 or url in seen or "VIDE" in url:
            continue
        seen.add(url)
        links.append((title, url))
    return links


def extract_cctv_article(html: str, fallback_title: str) -> tuple[str, str | None, str | None]:
    content = clean_html_text(
        extract_between(
            html,
            r'<div[^>]+class=["\']content_area["\'][^>]*>',
            ['<div class="function"', '<div class="zdfy"', '<div class="page_bottom"', '<div id="page_bottom"'],
        )
    )
    published_at = meta_content(html, "publishdate")
    if not published_at:
        date_match = re.search(r"(\d{4}年\d{2}月\d{2}日\s+\d{2}:\d{2})", html)
        published_at = parse_local_datetime(date_match.group(1)) if date_match else None
    author = meta_content(html, "source") or meta_content(html, "author")
    return content or fallback_title, published_at, author


def fetch_cctv_finance(source: dict[str, Any], entrypoint: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = entrypoint["url"]
    max_items = int(entrypoint.get("max_items", 20))
    fetch_detail = bool(entrypoint.get("fetch_detail", True))
    crawled_at = now_cn().isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    list_html = decode_html(fetch_url(url, timeout=timeout))
    links = parse_cctv_home(list_html, url)[:max_items]
    list_size = len(links)
    for list_rank, (title, article_url) in enumerate(links, 1):
        content = title
        published_at = date_from_url(article_url)
        author = None
        engagement_metrics = extract_engagement_metrics(None, crawled_at, "not_available")
        if fetch_detail:
            try:
                detail_html = decode_html(fetch_url(article_url, timeout=timeout))
                content, detail_published_at, author = extract_cctv_article(detail_html, title)
                published_at = detail_published_at or published_at
                engagement_metrics = extract_engagement_metrics(detail_html, crawled_at, "cctv_finance_detail_html")
            except Exception as exc:  # noqa: BLE001
                errors.append({"source": source.get("name"), "source_id": source.get("source_id"), "url": article_url, "error": str(exc), "crawled_at": crawled_at})
        records.append(
            {
                "source": source.get("name") or "央视财经",
                "source_id": source.get("source_id") or "cctv_finance",
                "title": title,
                "content": content,
                "url": article_url,
                "published_at": published_at,
                "crawled_at": crawled_at,
                "author": author,
                "section": "财经首页",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": url,
                "hot_features": build_hot_features(source, url, crawled_at, list_rank, list_size, engagement_metrics),
            }
        )
    return records, errors


def fetch_cls_telegraph(source: dict[str, Any], entrypoint: dict[str, Any], timeout: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """财联社电报快讯采集器。

    财联社提供公开的 nodeapi 接口，返回 JSON，无需登录。
    入口 type=cls_telegraph。
    """
    base_url = entrypoint.get("url", "https://www.cls.cn/nodeapi/updateTelegraphList")
    max_items = int(entrypoint.get("max_items", 30))
    crawled_at = now_cn().isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    params = {
        "app": "CLS.PC",
        "os": "web",
        "sv": "7.7.5",
        "rn": str(max_items),
        "lastTime": "0",
        "category": "",
        "_": str(int(datetime.now().timestamp() * 1000)),
    }
    url = base_url + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.cls.cn/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        payload = urllib.request.urlopen(req, timeout=timeout).read()
        obj = json.loads(payload.decode("utf-8", errors="replace"))
        items = (obj.get("data", {}).get("roll_data") or [])[:max_items]
        list_size = len(items)
        for list_rank, item in enumerate(items, 1):
            title = str(item.get("title") or item.get("brief") or "").strip()
            content = str(item.get("content") or item.get("brief") or title).strip()
            content = re.sub(r"<[^>]+>", " ", content)  # strip inline HTML
            content = re.sub(r"\s+", " ", content).strip()
            ts = item.get("ctime") or item.get("modified_time")
            published_at = None
            if ts:
                try:
                    published_at = datetime.fromtimestamp(int(ts), tz=CN_TZ).isoformat()
                except Exception:  # noqa: BLE001
                    pass
            article_id = str(item.get("id") or "")
            art_url = f"https://www.cls.cn/detail/{article_id}" if article_id else "https://www.cls.cn/"
            records.append({
                "source": source.get("name") or "财联社",
                "source_id": source.get("source_id") or "cls",
                "title": title,
                "content": content,
                "url": art_url,
                "published_at": published_at,
                "crawled_at": crawled_at,
                "author": item.get("author") or "",
                "section": "电报",
                "keywords": [],
                "raw_html_path": None,
                "fetch_entrypoint": base_url,
                "hot_features": build_hot_features(
                    source, base_url, crawled_at, list_rank, list_size,
                    extract_engagement_metrics(None, crawled_at, "cls_api", None),
                ),
            })
    except Exception as exc:  # noqa: BLE001
        errors.append({
            "source": source.get("name") or source.get("source_id") or "财联社",
            "source_id": source.get("source_id"),
            "url": url,
            "error": str(exc),
            "crawled_at": crawled_at,
        })
    return records, errors


def fetch_registry_rss(registry: dict[str, Any], enabled_only: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    timeout = int(registry.get("crawl_policy", {}).get("timeout_seconds", 15))
    crawled_at = now_cn().isoformat()

    for source in registry.get("sources", []):
        if enabled_only and not source.get("enabled_for_v1"):
            continue
        for entrypoint in source.get("entrypoints", []):
            if entrypoint.get("type") not in {"rss", "rss_feed", "atom"}:
                continue
            url = entrypoint.get("url")
            if not url:
                continue
            try:
                payload = fetch_url(url, timeout=timeout)
                records.extend(parse_rss_or_atom(payload, source, url, crawled_at))
            except Exception as exc:  # noqa: BLE001 - surfacing fetch failures in report data is intentional.
                errors.append(
                    {
                        "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                        "source_id": source.get("source_id"),
                        "url": url,
                        "error": str(exc),
                        "crawled_at": crawled_at,
                    }
                )

    return records, errors


def fetch_registry_html(registry: dict[str, Any], enabled_only: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    timeout = int(registry.get("crawl_policy", {}).get("timeout_seconds", 15))

    for source in registry.get("sources", []):
        if enabled_only and not source.get("enabled_for_v1"):
            continue
        for entrypoint in source.get("entrypoints", []):
            entry_type = entrypoint.get("type")
            supported_types = {"html_roll_list", "eastmoney_api", "stcn_article_list", "cctv_finance_home", "cls_telegraph"}
            if entry_type not in supported_types:
                continue
            try:
                if entry_type == "html_roll_list" and source.get("source_id") == "sina_finance":
                    source_records, source_errors = fetch_sina_roll(source, entrypoint, timeout)
                    records.extend(source_records)
                    errors.extend(source_errors)
                elif entry_type == "eastmoney_api":
                    source_records, source_errors = fetch_eastmoney_api(source, entrypoint, timeout)
                    records.extend(source_records)
                    errors.extend(source_errors)
                elif entry_type == "stcn_article_list":
                    source_records, source_errors = fetch_stcn_articles(source, entrypoint, timeout)
                    records.extend(source_records)
                    errors.extend(source_errors)
                elif entry_type == "cctv_finance_home":
                    source_records, source_errors = fetch_cctv_finance(source, entrypoint, timeout)
                    records.extend(source_records)
                    errors.extend(source_errors)
                elif entry_type == "cls_telegraph":
                    source_records, source_errors = fetch_cls_telegraph(source, entrypoint, timeout)
                    records.extend(source_records)
                    errors.extend(source_errors)
                else:
                    errors.append(
                        {
                            "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                            "source_id": source.get("source_id"),
                            "url": entrypoint.get("url"),
                            "error": f"unsupported entry_type={entry_type} source_id={source.get('source_id')}",
                            "crawled_at": now_cn().isoformat(),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "source": source.get("name") or source.get("source_id") or "UNKNOWN",
                        "source_id": source.get("source_id"),
                        "url": entrypoint.get("url"),
                        "error": str(exc),
                        "crawled_at": now_cn().isoformat(),
                    }
                )

    return records, errors
