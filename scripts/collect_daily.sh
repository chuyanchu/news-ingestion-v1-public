#!/usr/bin/env bash
# 每日采集脚本：抓取当日新闻并写入 data/daily/YYYYMMDD/
# 由 launchd（com.news-ingestion.daily）在每个交易日收盘后调用。
set -e
ROOT="/Users/cyc/Desktop/相关文档/00-项目/量化交易/news-ingestion-v1"
cd "$ROOT"

# 加载 .env（DeepSeek key 等；采集本身不强依赖，但保持一致）
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export PYTHONPATH="$ROOT/src"
mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/collect.log"

echo "[$(date '+%F %T')] ── daily collect start ──" >> "$LOG"
python3 -m news_ingestion.cli daily --analyze >> "$LOG" 2>&1 && \
  echo "[$(date '+%F %T')] daily collect OK" >> "$LOG" || \
  echo "[$(date '+%F %T')] daily collect FAILED" >> "$LOG"
