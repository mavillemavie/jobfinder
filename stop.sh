#!/usr/bin/env bash
# Stop whatever listens on the dashboard port. Usage: ./stop.sh
set -euo pipefail
cd "$(dirname "$0")"
PORT=$(grep -E '^\s*port:' config/profile.yaml | head -1 | sed 's/[^0-9]//g'); PORT=${PORT:-3838}
PIDS=$(ss -ltnp "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
if [ -z "$PIDS" ]; then echo "nothing listening on :$PORT"; rm -f data/server.pid; exit 0; fi
for p in $PIDS; do kill "$p" 2>/dev/null || true; done
sleep 1; rm -f data/server.pid; echo "stopped ($PIDS)"
