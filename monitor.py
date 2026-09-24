#!/usr/bin/env python3
"""Serve the Phase 1 AI Agent Monitor dashboard locally."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/dashboard.html"):
            self.send_error(404, "Not Found")
            return

        try:
            content = DASHBOARD_PATH.read_bytes()
        except FileNotFoundError:
            self.send_error(500, "dashboard.html is missing")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve the AI Agent Monitor dashboard on this computer."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Local port to use (default: {DEFAULT_PORT})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not DASHBOARD_PATH.is_file():
        raise SystemExit(f"dashboard.html was not found at {DASHBOARD_PATH}")

    server = ThreadingHTTPServer((HOST, args.port), DashboardHandler)
    url = f"http://{HOST}:{args.port}"

    print(f"AI Agent Monitor is running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Agent Monitor.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
