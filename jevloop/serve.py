"""Loopback-only dashboard server with an explicit path allow-list."""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver
from pathlib import Path
from urllib.parse import urlsplit

LOG_DIR = Path(os.getenv("JEV_LOOP_HOME", str(Path.home() / ".jev-loop")))
PACKAGE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = PACKAGE_DIR / "assets" / "dashboard"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(DASHBOARD_DIR / "index.html", "text/html; charset=utf-8")
            return
        if path == "/wall.html":
            self._send_file(DASHBOARD_DIR / "wall.html", "text/html; charset=utf-8")
            return
        if path == "/latest.json":
            latest = LOG_DIR / "latest.json"
            if not latest.exists():
                self._send_bytes(b'{"schema_version":2,"ticks":[],"stats":{}}', "application/json")
            else:
                self._send_file(latest, "application/json")
            return
        self.send_error(404)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self._send_bytes(data, content_type)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):  # noqa: A002
        return


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jev-loop serve")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with ReusableTCPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"dashboard: http://127.0.0.1:{args.port}/")
        print("exposed paths: /, /wall.html, /latest.json only")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
