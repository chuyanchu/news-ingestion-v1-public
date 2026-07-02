"""新闻热度自相关 / 持续性回测。

本模块只使用新闻数据本身，不依赖任何行情数据（行情验证属于另一个模块）。
它回答的问题是：
  1. 持续性：今天热的板块，明天是否还热？（热度是否有惯性）
  2. 排名稳定性：同一板块跨日的热度排名变化有多大？
  3. 交易池前瞻命中：交易池选出的板块，次日是否仍在热榜前列？

这些指标验证的是"新闻热度信号的持续性"，不是"是否真能赚钱"——后者需要行情数据，
将来可与行情模块跨模块对接。
"""
from __future__ import annotations

from typing import Any

from .sector_heat import build_sector_heatmap, build_trade_pool


def _top_sectors(heatmap: list[dict[str, Any]], top_n: int) -> list[str]:
    return [item["sector"] for item in heatmap[:top_n]]


def run_backtest(
    dated_records: list[tuple[str, list[dict[str, Any]]]],
    sector_dict: list[dict[str, Any]],
    *,
    top_n: int = 5,
    pool_days: int = 3,
) -> dict[str, Any]:
    """计算跨日热度持续性指标。

    dated_records 必须按日期升序排列，每项为 (date_tag, records)。
    """
    # 预先算好每天的热力图（排名）
    daily_heat: list[tuple[str, list[dict[str, Any]]]] = []
    for date_tag, records in dated_records:
        heatmap = build_sector_heatmap(records, sector_dict, limit=200)
        daily_heat.append((date_tag, heatmap))

    transitions: list[dict[str, Any]] = []
    persistence_rates: list[float] = []
    rank_deltas: list[float] = []

    for i in range(len(daily_heat) - 1):
        date_t, heat_t = daily_heat[i]
        date_t1, heat_t1 = daily_heat[i + 1]
        top_t = _top_sectors(heat_t, top_n)
        top_t1 = set(_top_sectors(heat_t1, top_n))
        rank_map_t1 = {item["sector"]: item["rank"] for item in heat_t1}
        rank_map_t = {item["sector"]: item["rank"] for item in heat_t}

        if not top_t:
            continue
        survived = [s for s in top_t if s in top_t1]
        rate = len(survived) / len(top_t)
        persistence_rates.append(rate)

        # 排名变化（同时出现在两天的板块）
        pair_deltas = []
        for sector in top_t:
            if sector in rank_map_t1:
                delta = rank_map_t1[sector] - rank_map_t[sector]  # 正=排名下滑
                pair_deltas.append(delta)
                rank_deltas.append(abs(delta))

        transitions.append({
            "from_date": date_t,
            "to_date": date_t1,
            "top_sectors": top_t,
            "survived": survived,
            "dropped": [s for s in top_t if s not in top_t1],
            "persistence_rate": round(rate, 3),
            "avg_abs_rank_delta": round(sum(abs(d) for d in pair_deltas) / len(pair_deltas), 2) if pair_deltas else None,
        })

    # 交易池前瞻命中：对每个有足够历史的日期 T，用 [..T] 构建交易池，看 top 池板块是否在 T+1 热榜前列
    pool_checks: list[dict[str, Any]] = []
    pool_hits: list[float] = []
    for i in range(len(daily_heat) - 1):
        # 用截止到第 i 天（含）的窗口构建交易池
        window = dated_records[max(0, i + 1 - pool_days): i + 1]
        if not window:
            continue
        pool = build_trade_pool(window, sector_dict, days=pool_days, limit=top_n)
        pool_sectors = [item["sector"] for item in pool[:top_n]]
        if not pool_sectors:
            continue
        next_top = set(_top_sectors(daily_heat[i + 1][1], top_n))
        hits = [s for s in pool_sectors if s in next_top]
        hit_rate = len(hits) / len(pool_sectors)
        pool_hits.append(hit_rate)
        pool_checks.append({
            "as_of_date": daily_heat[i][0],
            "next_date": daily_heat[i + 1][0],
            "pool_sectors": pool_sectors,
            "hit": hits,
            "hit_rate": round(hit_rate, 3),
        })

    # 每个板块的活跃天数 / 平均排名
    sector_stats: dict[str, dict[str, Any]] = {}
    for date_tag, heatmap in daily_heat:
        for item in heatmap[:20]:
            stat = sector_stats.setdefault(item["sector"], {"sector": item["sector"], "days": 0, "ranks": []})
            stat["days"] += 1
            stat["ranks"].append(item["rank"])
    sector_table = []
    for stat in sector_stats.values():
        ranks = stat["ranks"]
        sector_table.append({
            "sector": stat["sector"],
            "active_days": stat["days"],
            "avg_rank": round(sum(ranks) / len(ranks), 2),
            "best_rank": min(ranks),
        })
    sector_table.sort(key=lambda x: (-x["active_days"], x["avg_rank"]))

    n_days = len(daily_heat)
    summary = {
        "days_covered": n_days,
        "date_range": [daily_heat[0][0], daily_heat[-1][0]] if daily_heat else [],
        "top_n": top_n,
        "avg_persistence_rate": round(sum(persistence_rates) / len(persistence_rates), 3) if persistence_rates else None,
        "avg_rank_stability": round(sum(rank_deltas) / len(rank_deltas), 2) if rank_deltas else None,
        "avg_pool_hit_rate": round(sum(pool_hits) / len(pool_hits), 3) if pool_hits else None,
        "note": "仅基于新闻热度的持续性回测；不含行情收益验证（需行情模块）。",
    }

    return {
        "summary": summary,
        "transitions": transitions,
        "pool_checks": pool_checks,
        "sector_table": sector_table[:20],
    }
