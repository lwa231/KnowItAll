"""Local HTTP server for the UI. Python standard library only - no Flask, no Node."""
import json
import mimetypes
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import store
from .normalize import FIELDS
from .runner import runner
from .scraper import write_csv

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
OUTPUT_DIR = Path("output")

shutdown_hook = None      # set by ui.py so /api/quit can close Chrome and exit


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- plumbing ----------
    def log_message(self, *args):
        pass                                   # the scraper's own log is the interesting one

    def _local_only(self):
        # Bound to loopback already; this also blocks DNS-rebinding style Host headers.
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _send(self, body, content_type="application/json", status=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}

    # ---------- routes ----------
    def do_GET(self):
        if not self._local_only():
            return self._send({"error": "forbidden"}, status=403)
        route = urlparse(self.path)
        path, query = route.path, parse_qs(route.query)

        if path in ("/", "/index.html"):
            return self._file(UI_DIR / "index.html")
        if path.startswith("/ui/"):
            return self._file(UI_DIR / path[4:])

        if path == "/api/state":
            cursor = int((query.get("since") or ["0"])[0])
            events, next_cursor = runner.events_since(cursor)
            state = runner.snapshot()
            state.update(events=events, cursor=next_cursor)
            return self._send(state)

        if path == "/api/history":
            return self._send({"runs": store.history()})

        if path == "/api/run-jobs":
            run_id = int((query.get("id") or ["0"])[0])
            return self._send({"jobs": store.run_jobs(run_id)})

        if path == "/api/output":
            return self._send({"files": self._output_files()})

        return self._send({"error": "not found"}, status=404)

    def do_POST(self):
        if not self._local_only():
            return self._send({"error": "forbidden"}, status=403)
        path = urlparse(self.path).path
        data = self._body()

        if path == "/api/run":
            urls = [u for u in (data.get("urls") or []) if str(u).strip()]
            started = runner.start(urls, data.get("options") or {})
            return self._send({"started": started})

        if path == "/api/queue":
            return self._send({"added": runner.queue(data.get("urls") or [])})

        if path == "/api/stop":
            runner.stop()
            return self._send({"stopped": True})

        if path == "/api/open-folder":
            return self._send({"opened": self._open_folder()})

        if path == "/api/export":
            return self._send(self._export_all())

        if path == "/api/quit":
            self._send({"quitting": True})
            threading.Thread(target=self._quit, daemon=True).start()
            return

        return self._send({"error": "not found"}, status=404)

    # ---------- helpers ----------
    def _file(self, path):
        path = path.resolve()
        if not str(path).startswith(str(UI_DIR.resolve())) or not path.is_file():
            return self._send({"error": "not found"}, status=404)
        kind = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return self._send(path.read_bytes(), content_type=kind)

    def _output_files(self):
        if not OUTPUT_DIR.exists():
            return []
        files = []
        for item in sorted(OUTPUT_DIR.glob("jobs_*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
            rows = None
            try:
                if item.suffix == ".json":
                    rows = len(json.loads(item.read_text() or "[]"))
                elif item.suffix == ".csv":
                    rows = max(0, sum(1 for _ in item.open(encoding="utf-8-sig")) - 1)
            except Exception:
                rows = None
            files.append({
                "name": item.name, "rows": rows,
                "size": _human_size(item.stat().st_size),
                "written": item.stat().st_mtime,
            })
        return files

    def _open_folder(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        target = str(OUTPUT_DIR.resolve())
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
            elif os.name == "nt":
                os.startfile(target)                                   # noqa: S606 - Windows only
            else:
                subprocess.Popen(["xdg-open", target])
            return True
        except Exception:
            return False

    def _export_all(self):
        rows = []
        for domain in runner.order:
            state = runner.companies.get(domain) or {}
            run_id = state.get("run_id")
            if run_id:
                rows.extend(store.run_jobs(run_id))
        if not rows:
            return {"written": None, "rows": 0}
        OUTPUT_DIR.mkdir(exist_ok=True)
        path = OUTPUT_DIR / "jobs_all_companies.csv"
        write_csv([{k: r.get(k) for k in FIELDS} for r in rows], str(path))
        return {"written": str(path), "rows": len(rows)}

    def _quit(self):
        if shutdown_hook:
            shutdown_hook()


def _human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def serve():
    """Start the server on a free loopback port. Returns (httpd, url)."""
    store.init()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/"
