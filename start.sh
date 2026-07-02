#!/usr/bin/env bash
# Quick-start for macOS — loads .env and starts the API server
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export PYTHONPATH="$ROOT/src"
PORT="${PORT:-19080}"

echo "Starting news-ingestion API on http://127.0.0.1:${PORT}"
echo "AI provider: ${DEEPSEEK_API_KEY:+DeepSeek}${OPENAI_API_KEY:+OpenAI}"
exec python3 -m news_ingestion.api_server --port "$PORT" "$@"
