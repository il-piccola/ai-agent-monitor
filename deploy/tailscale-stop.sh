#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
PORTS_FILE="$RUNTIME_DIR/ports.env"
PID_FILE="$RUNTIME_DIR/monitor.pid"

if [[ ! -f "$PORTS_FILE" ]]; then
  echo "No deployment state found at $PORTS_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$PORTS_FILE"

if command -v tailscale >/dev/null 2>&1; then
  tailscale serve     --https="$TAILSCALE_HTTPS_PORT"     "http://127.0.0.1:$BACKEND_PORT"     off || true
fi

if [[ -f "$PID_FILE" ]]; then
  MONITOR_PID="$(cat "$PID_FILE")"
  kill "$MONITOR_PID" >/dev/null 2>&1 || true
fi

rm -f "$PID_FILE" "$PORTS_FILE"
echo "AI Agent Monitor deployment stopped."
