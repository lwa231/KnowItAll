"""Local HTTP server for the UI. Python standard library only - no Flask, no Node."""
import hmac
import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import paths, store
from .fetch import log_exception
from .service import Service

UI_DIR = paths.asset_dir() / "ui"

shutdown_hook = None      # set by ui.py so /api/quit can close the window and exit

HEARTBEAT_SECONDS = 10     # a comment line keeps an idle event stream from being dropped by proxies/browsers
STREAM_BATCH_SECONDS = 0.05    # after a wake-up, let a burst of events land so they go out as one message
MAX_BODY = 1_000_000      # bytes; a list of company addresses is tiny
TOKEN_HEADER = "X-KnowItAll-Token"
TOKEN_PLACEHOLDER = "__KNOWITALL_TOKEN__"
THEME_PLACEHOLDER = "__KNOWITALL_THEME__"
TOKEN = secrets.token_urlsafe(32)   # fresh every launch; only pages this server itself served know it


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def service(self):
        return self.server.service

    # ---------- plumbing ----------
    def log_message(self, *args):
        pass                                   # the scraper's own log is the interesting one

    def _local_only(self):
        # Bound to loopback already; this also blocks DNS-rebinding style Host headers.
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _allowed_origin(self):
        """Browsers attach Origin to cross-site requests (and to fetch POSTs). Ours is exactly this server."""
        origin = self.headers.get("Origin")
        if origin is None:
            return True                                    # not a browser cross-site request (curl, our own GETs)
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _authorized(self):
        """Every /api call must come from our own page: right Origin, right per-launch token."""
        if not self._allowed_origin():
            return False
        supplied = self.headers.get(TOKEN_HEADER) or ""
        return hmac.compare_digest(supplied.encode(), TOKEN.encode())

    def _reject(self, status, message):
        # A rejected POST leaves its body unread; closing the connection keeps it from being
        # misread as the start of the next request on a keep-alive socket.
        self.close_connection = True
        return self._send({"error": message}, status=status, close=True)

    def _int(self, query, name, default=0):
        """An integer query parameter; a malformed one is the caller's mistake (400), not a server error."""
        try:
            return int((query.get(name) or [str(default)])[0])
        except ValueError:
            raise ValueError(f"{name} must be a whole number") from None

    def _stream(self, since):
        """Server-sent events: the whole state, pushed whenever something happens (and once at the start)."""
        self.close_connection = True
        self.send_response(200)
        for header, value in (("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store"),
                              ("Connection", "close"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(header, value)
        self.end_headers()
        runner = self.service.runner
        cursor, first, last_write = max(0, since), True, time.time()
        try:
            while not self.server.stopping.is_set():
                if not first:
                    events, _ = runner.wait_for_events(cursor, timeout=1.0)
                    if events:
                        time.sleep(STREAM_BATCH_SECONDS)
                else:
                    events = True
                if events:
                    state = self.service.state(cursor)
                    self.wfile.write(b"data: " + json.dumps(state).encode() + b"\n\n")
                    self.wfile.flush()
                    cursor, first, last_write = state["cursor"], False, time.time()
                elif time.time() - last_write >= HEARTBEAT_SECONDS:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_write = time.time()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return                                              # the window closed or navigated away

    def _send(self, body, content_type="application/json", status=200, close=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        """The JSON object sent with a POST ({} when empty), or None if it isn't a valid JSON object."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return {}
        if length > MAX_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    # ---------- routes ----------
    def _guarded(self, route):
        """Every route runs inside this: an unexpected error answers 500 (and is logged) instead of dropping the connection."""
        try:
            return route()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            raise
        except Exception:
            log_exception(f"{self.command} {self.path} failed")
            try:
                self.close_connection = True
                self._send({"error": "internal error; details are in the log"}, status=500, close=True)
            except OSError:
                pass

    def do_GET(self):
        return self._guarded(self._get)

    def do_POST(self):
        return self._guarded(self._post)

    def _get(self):
        if not self._local_only():
            return self._reject(403, "forbidden")
        route = urlparse(self.path)
        path, query = route.path, parse_qs(route.query)

        if path.startswith("/api/") and not self._authorized():
            return self._reject(403, "forbidden")
        if path in ("/", "/index.html"):
            return self._index()
        if path.startswith("/ui/"):
            return self._file(UI_DIR / path[4:])

        try:
            if path == "/api/stream":
                return self._stream(self._int(query, "since"))
            if path == "/api/state":
                return self._send(self.service.state(self._int(query, "since")))
            if path == "/api/run-jobs":
                return self._send(self.service.run_jobs(self._int(query, "id")))
        except ValueError as error:
            return self._send({"error": str(error)}, status=400)

        if path == "/api/settings":
            return self._send(self.service.get_settings())

        if path == "/api/geo/countries":
            return self._send(self.service.countries())

        if path == "/api/system":
            return self._send(self.service.system_info())

        if path == "/api/jobs":
            try:
                return self._send(self.service.query_jobs(query))
            except ValueError as error:                       # bad filter value: the caller's mistake, not ours
                return self._send({"error": str(error)}, status=400)

        if path == "/api/history":
            try:
                query.pop("limit", None)
                mode = (query.pop("session", None) or ["current"])[0]
                has_filters = any(v for k, v in query.items() if k != "session_id")
                return self._send(self.service.history(session=mode, filters=query if has_filters else None))
            except ValueError as error:
                return self._send({"error": str(error)}, status=400)

        if path == "/api/backups":
            return self._send({"backups": self.service.list_backups()})

        if path == "/api/maintenance":
            return self._send(self.service.maintenance_status())

        if path == "/api/output":
            return self._send({"files": self.service.list_exports()})

        return self._send({"error": "not found"}, status=404)

    def _post(self):
        if not self._local_only():
            return self._reject(403, "forbidden")
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._reject(404, "not found")
        if not self._authorized():
            return self._reject(403, "forbidden")
        # Only application/json: a cross-site form or fetch can't send it without a CORS preflight,
        # which this server never grants.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self._reject(415, "content type must be application/json")
        data = self._body()
        if data is None:
            return self._reject(400, "body must be a JSON object")

        try:
            if path == "/api/scan":
                return self._send(self.service.scan(self._urls(data)))
            if path == "/api/start":
                return self._send({"started": self.service.start_ready()})
            if path == "/api/backups":
                return self._send({"backup": self.service.create_backup("manual")})
            if path == "/api/backups/open-folder":
                return self._send({"opened": self.service.open_backups()})
            if path == "/api/filters":
                return self._send({"filters": self.service.set_filters(data.get("filters"))})
            if path == "/api/parallel":
                return self._send(self.service.set_parallel(data.get("n")))
            if path == "/api/run":                                    # older clients and the command line's shape
                started = self.service.start(self._urls(data), data.get("options") or {})
                return self._send({"started": started})
            if path == "/api/queue":
                return self._send({"added": self.service.queue(self._urls(data))})
        except ValueError as error:                                   # a bad value is the caller's mistake, not ours
            return self._send({"error": str(error)}, status=400)

        if path == "/api/stop":
            domain = data.get("domain") or None
            if domain is not None and not isinstance(domain, str):
                return self._send({"error": "domain must be text"}, status=400)
            return self._send({"stopped": bool(self.service.stop(domain))})

        if path == "/api/settings":
            try:
                return self._send(self.service.update_settings(data))
            except ValueError as error:
                return self._send({"error": str(error)}, status=400)

        if path == "/api/maintenance/clear-cache":
            result = self.service.clear_cache()
            if result is None:
                return self._send({"error": "a scan is running; clear the cache when it has finished"}, status=409)
            return self._send(result)

        if path == "/api/maintenance/compact":
            result = self.service.compact_database()
            if result is None:
                return self._send({"error": "a scan is running; try again when it has finished"}, status=409)
            return self._send(result)

        if path == "/api/open-folder":
            return self._send({"opened": self.service.open_exports()})

        if path == "/api/export":
            filters = data.get("filters")
            try:
                return self._send(self.service.export_session(filters if isinstance(filters, dict) else None))
            except ValueError as error:
                return self._send({"error": str(error)}, status=400)

        if path == "/api/quit":
            self._send({"quitting": True})
            threading.Thread(target=self._quit, daemon=True).start()
            return

        return self._send({"error": "not found"}, status=404)

    @staticmethod
    def _urls(data):
        urls = data.get("urls") or []
        if not isinstance(urls, list) or len(urls) > 200:
            raise ValueError("urls must be a list of at most 200 addresses")
        return [str(u).strip() for u in urls if str(u).strip()]

    # ---------- helpers ----------
    def _index(self):
        theme = self.service.get_settings()["theme"]              # known before first paint: no flash of the wrong theme
        page = (UI_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace(TOKEN_PLACEHOLDER, TOKEN).replace(THEME_PLACEHOLDER, theme)
        return self._send(page, content_type="text/html; charset=utf-8")

    def _file(self, path):
        path = path.resolve()
        if not path.is_relative_to(UI_DIR.resolve()) or not path.is_file():
            return self._send({"error": "not found"}, status=404)
        kind = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        return self._send(path.read_bytes(), content_type=kind)

    def _quit(self):
        if shutdown_hook:
            shutdown_hook()


def serve(service=None):
    """Start the server on a free loopback port. Returns (httpd, url)."""
    global TOKEN
    TOKEN = secrets.token_urlsafe(32)              # a new secret for every server that is started
    store.init()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.service = service or Service()
    httpd.stopping = threading.Event()
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/"


def shutdown(httpd):
    """Stop serving, and let any open event streams end instead of waiting for their next heartbeat."""
    httpd.stopping.set()
    httpd.shutdown()
