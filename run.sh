#!/usr/bin/env bash
# Start the jobfinder dashboard + scheduler (single instance, ss-guarded). Usage: ./run.sh [--no-open]
set -euo pipefail
cd "$(dirname "$0")"
PORT=$(grep -E '^\s*port:' config/profile.yaml | head -1 | sed 's/[^0-9]//g'); PORT=${PORT:-3838}
mkdir -p data/logs && chmod 700 data && { [ -f .env ] && chmod 600 .env || true; }
if ss -ltn "sport = :$PORT" | grep -q LISTEN; then
  echo "jobfinder already listening on :$PORT"
  [ "${1:-}" = "--no-open" ] || xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
  exit 0
fi
command -v uv >/dev/null || { echo "uv not found"; exit 1; }
uv sync --quiet
uv run jobfinder db upgrade
nohup uv run jobfinder serve >> data/logs/server.log 2>&1 &
echo $! > data/server.pid
for _ in $(seq 1 30); do ss -ltn "sport = :$PORT" | grep -q LISTEN && break; sleep 1; done
if ss -ltn "sport = :$PORT" | grep -q LISTEN; then
  echo "jobfinder up on http://localhost:$PORT (pid $(cat data/server.pid))"
  [ "${1:-}" = "--no-open" ] || xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || true
else
  echo "failed to start; see data/logs/server.log"; tail -20 data/logs/server.log; exit 1
fi
