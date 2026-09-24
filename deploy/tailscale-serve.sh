#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
mkdir -p "$RUNTIME_DIR"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

require_command python3
require_command tailscale

port_is_free() {
  local host="$1"
  local port="$2"
  python3 - "$host" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

TAILSCALE_SERVE_STATUS="$(tailscale serve status --json 2>/dev/null || printf '{}')"

tailscale_port_is_unused() {
  local port="$1"

  if ! port_is_free "0.0.0.0" "$port"; then
    return 1
  fi

  # Skip any port number already mentioned in Tailscale Serve configuration.
  if printf '%s' "$TAILSCALE_SERVE_STATUS" |
    grep -Eq "(^|[^0-9])${port}([^0-9]|$)"; then
    return 1
  fi

  return 0
}

find_backend_port() {
  local port
  for port in $(seq 8765 8799); do
    if port_is_free "127.0.0.1" "$port"; then
      printf '%s\n' "$port"
      return 0
    fi
  done

  echo "No free backend port found in 8765-8799." >&2
  return 1
}

find_tailscale_https_port() {
  local port

  # 443 is Tailscale Serve's normal HTTPS port and 8443 is commonly used.
  # Both are intentionally excluded for this deployment.
  for port in $(seq 9443 9499) $(seq 10443 10499); do
    if tailscale_port_is_unused "$port"; then
      printf '%s\n' "$port"
      return 0
    fi
  done

  echo "No free Tailscale HTTPS port found in the configured ranges." >&2
  return 1
}

BACKEND_PORT="$(find_backend_port)"
TAILSCALE_HTTPS_PORT="$(find_tailscale_https_port)"

LOG_FILE="$RUNTIME_DIR/monitor.log"
PID_FILE="$RUNTIME_DIR/monitor.pid"
PORTS_FILE="$RUNTIME_DIR/ports.env"

echo "Starting AI Agent Monitor on localhost:${BACKEND_PORT} ..."
nohup python3 "$ROOT_DIR/monitor.py" --port "$BACKEND_PORT"   >"$LOG_FILE" 2>&1 &
MONITOR_PID=$!
printf '%s\n' "$MONITOR_PID" >"$PID_FILE"

cleanup_on_error() {
  kill "$MONITOR_PID" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
}
trap cleanup_on_error ERR

python3 - "$BACKEND_PORT" <<'PY'
import sys
import time
import urllib.request

port = int(sys.argv[1])
url = f"http://127.0.0.1:{port}/"

last_error = None
for _ in range(30):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(0.2)

print(f"Monitor did not become ready: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY

echo "Configuring Tailscale Serve HTTPS on port ${TAILSCALE_HTTPS_PORT} ..."
SERVE_OUTPUT="$(
  tailscale serve     --bg     --yes     --https="$TAILSCALE_HTTPS_PORT"     "http://127.0.0.1:$BACKEND_PORT"
)"

cat >"$PORTS_FILE" <<EOF
BACKEND_PORT=$BACKEND_PORT
TAILSCALE_HTTPS_PORT=$TAILSCALE_HTTPS_PORT
MONITOR_PID=$MONITOR_PID
EOF

trap - ERR

printf '%s\n' "$SERVE_OUTPUT"
echo
echo "Deployment complete."
echo "Backend: http://127.0.0.1:${BACKEND_PORT}"
echo "Tailscale HTTPS port: ${TAILSCALE_HTTPS_PORT}"
echo "Runtime details: ${PORTS_FILE}"
echo "Server log: ${LOG_FILE}"
