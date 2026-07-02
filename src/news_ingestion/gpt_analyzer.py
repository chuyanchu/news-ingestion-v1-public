from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any


# ── provider defaults ──────────────────────────────────────────────────────────
_DEEPSEEK_BASE = "https://api.deepseek.com/v1"
_DEEPSEEK_MODEL = "deepseek-chat"
_OPENAI_BASE = "https://api.openai.com/v1"
_OPENAI_MODEL = "gpt-4o-mini"

_SYSTEM_PROMPT = "你是A股财经新闻分析助手，输出必须是可解析JSON，不含Markdown代码块。"

_TASK_SCHEMA = {
    "summary": "不超过60字的中文摘要",
    "impact": "positive|negative|neutral",
    "importance": "0到100整数，越重要越高",
    "sectors": ["只能从 candidate_sectors 里原样选取；都不相关就返回空数组[]，不要自创名称"],
    "suggested_sector": "当 candidate_sectors 都不合适、但确实属于某个清晰行业主题时，给一个简短的新板块名（如'通信''稀土'）；否则空字符串",
    "stocks": [{"name": "新闻明确点名的A股上市公司简称（如'寒武纪''比亚迪'），没有点名具体公司就返回空数组", "code": "6位股票代码，确定才填否则空字符串"}],
    "reason": "进入交易池或不进入的理由，不超过50字",
    "catalyst": "核心催化逻辑，不超过40字",
}


def _active_provider() -> str:
    """Return 'deepseek', 'openai', or 'none'."""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "none"


def ai_enabled() -> bool:
    return _active_provider() != "none"


# kept for backwards-compat
def gpt_enabled() -> bool:
    return ai_enabled()


def provider_info() -> dict[str, Any]:
    provider = _active_provider()
    if provider == "deepseek":
        return {
            "provider": "deepseek",
            "model": os.environ.get("DEEPSEEK_MODEL", _DEEPSEEK_MODEL),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", _DEEPSEEK_BASE),
            "enabled": True,
        }
    if provider == "openai":
        return {
            "provider": "openai",
            "model": os.environ.get("OPENAI_MODEL", _OPENAI_MODEL),
            "base_url": os.environ.get("OPENAI_BASE_URL", _OPENAI_BASE),
            "enabled": True,
        }
    return {"provider": "none", "enabled": False, "note": "Set DEEPSEEK_API_KEY or OPENAI_API_KEY to enable AI analysis."}


def _call_chat(
    messages: list[dict[str, str]],
    *,
    provider: str,
    timeout: int = 25,
) -> str:
    if provider == "deepseek":
        api_key = os.environ["DEEPSEEK_API_KEY"]
        base_url = os.environ.get("DEEPSEEK_BASE_URL", _DEEPSEEK_BASE).rstrip("/")
        model = os.environ.get("DEEPSEEK_MODEL", _DEEPSEEK_MODEL)
    else:
        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ.get("OPENAI_BASE_URL", _OPENAI_BASE).rstrip("/")
        model = os.environ.get("OPENAI_MODEL", _OPENAI_MODEL)

    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "messages": messages,
    }
    # DeepSeek supports response_format; OpenAI too — safe to always set
    body["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def analyze_article(
    title: str,
    content: str,
    sectors: list[str],
    *,
    timeout: int = 25,
) -> dict[str, Any]:
    """Analyze a news article with the active AI provider.

    Falls back to a no-op dict when no API key is configured.
    Supports both DeepSeek and OpenAI via the same OpenAI-compatible chat API.
    """
    provider = _active_provider()
    if provider == "none":
        return {"enabled": False, "summary": "", "impact": "neutral", "importance": 0,
                "reason": "no API key", "catalyst": "", "sectors": []}

    user_msg = json.dumps(
        {
            "title": title,
            "content": content[:1500],
            "candidate_sectors": sectors,
            "task": "判断财经新闻的重要性、影响方向、关联板块和点名的个股。sectors 只能从 candidate_sectors 里原样选，都不相关就留空并在 suggested_sector 里给建议。stocks 只填新闻明确点名的A股上市公司。只输出JSON。",
            "schema": _TASK_SCHEMA,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    raw = _call_chat(messages, provider=provider, timeout=timeout)
    parsed = json.loads(raw)
    return {
        "enabled": True,
        "provider": provider,
        "summary": str(parsed.get("summary") or "")[:120],
        "impact": parsed.get("impact") if parsed.get("impact") in {"positive", "negative", "neutral"} else "neutral",
        "importance": max(0, min(100, int(parsed.get("importance") or 0))),
        "sectors": parsed.get("sectors") if isinstance(parsed.get("sectors"), list) else [],
        "suggested_sector": str(parsed.get("suggested_sector") or "")[:20],
        "stocks": _normalize_stocks(parsed.get("stocks")),
        "reason": str(parsed.get("reason") or "")[:120],
        "catalyst": str(parsed.get("catalyst") or "")[:100],
    }


def _normalize_stocks(raw: Any) -> list[dict[str, str]]:
    """把 AI 返回的 stocks 归一化成 [{name, code}]，容错字符串列表/对象列表两种格式。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            code = str(item.get("code") or "").strip()
        else:
            name, code = str(item or "").strip(), ""
        code = code if code.isdigit() and len(code) == 6 else ""
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name[:20], "code": code})
    return out


# backwards-compat alias used by existing api_server.py call
def analyze_article_with_gpt(
    title: str,
    content: str,
    sectors: list[str],
    *,
    timeout: int = 25,
) -> dict[str, Any]:
    return analyze_article(title, content, sectors, timeout=timeout)


# ── persistence ─────────────────────────────────────────────────────────────────
def ai_analysis_path(data_root: Path, date_tag: str) -> Path:
    return Path(data_root) / date_tag / "ai_analysis.json"


def load_ai_analysis(data_root: Path, date_tag: str) -> dict[str, Any]:
    """读取某日已持久化的 AI 分析，返回 {article_id: analysis}。无则空 dict。"""
    path = ai_analysis_path(data_root, date_tag)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def save_ai_analysis(data_root: Path, date_tag: str, analysis_map: dict[str, Any]) -> None:
    path = ai_analysis_path(data_root, date_tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analysis_map, ensure_ascii=False, indent=2), encoding="utf-8")


def batch_analyze_for_date(
    records: list[dict[str, Any]],
    sector_dict: list[dict[str, Any]],
    data_root: Path,
    date_tag: str,
    *,
    max_articles: int = 200,
    timeout: int = 25,
) -> dict[str, Any]:
    """对某日所有命中板块的新闻批量跑 AI（增量+缓存，已分析过的跳过）。

    供采集后自动调用，也供 /api/v1/gpt/batch-analyze 复用。返回统计信息。
    """
    # 局部导入，避免与 sector_heat 形成顶层循环依赖
    from .sector_heat import article_hot_score, dedup_articles, filter_recent

    if not ai_enabled():
        return {"enabled": False, "analyzed": 0, "errors": 0, "matched": 0, "cached": 0}

    # 分析全部近期新闻（不止命中板块的）：这样 AI 能给未匹配新闻兜底归类、并抽取个股。
    recs = dedup_articles(filter_recent(records, date_tag))
    recs.sort(key=article_hot_score, reverse=True)
    sector_names = [str(s.get("name")) for s in sector_dict if s.get("name")]

    cache = load_ai_analysis(data_root, date_tag)
    analyzed = errors = 0
    for record in recs[:max_articles]:
        aid = str(record.get("article_id") or "")
        if not aid or (aid in cache and cache[aid].get("enabled")):
            continue
        try:
            cache[aid] = analyze_article(
                str(record.get("title") or ""),
                str(record.get("content") or ""),
                sector_names,
                timeout=timeout,
            )
            analyzed += 1
        except Exception as exc:  # noqa: BLE001
            cache[aid] = {"enabled": False, "error": str(exc)}
            errors += 1
    if analyzed or errors:
        save_ai_analysis(data_root, date_tag, cache)
    return {"enabled": True, "analyzed": analyzed, "errors": errors,
            "candidates": len(recs), "cached": len(cache)}
