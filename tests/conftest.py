import http.client
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

# Safety first: no test may read or move the developer's real data (history.db, cache, exports).
# Must run before anything imports knowitall.paths, which reads these at import time.
_SANDBOX = Path(tempfile.mkdtemp(prefix="knowitall-tests-"))
os.environ["KNOWITALL_HOME"] = str(_SANDBOX / "home")
os.environ["KNOWITALL_LEGACY_DIR"] = str(_SANDBOX / "no-legacy-data")
os.environ.pop("KNOWITALL_EXPORTS", None)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def fake_fetch(monkeypatch):
    """Route an ATS module's fetch_json / fetch_json_many to canned responses.

    Usage: fake_fetch(module, {"url-substring": data_or_(status, data)}) - the first key found
    in the requested URL wins; anything unmatched returns HTTP 404.
    """
    def install(module, routes):
        def respond(url):
            for needle, value in routes.items():
                if needle in url:
                    status, data = value if isinstance(value, tuple) else (200, value)
                    return {"status": status, "data": data}
            return {"status": 404, "data": None}

        monkeypatch.setattr(module, "fetch_json", lambda ctx, url, method="GET", json=None: respond(url))
        if hasattr(module, "fetch_json_many"):
            monkeypatch.setattr(
                module, "fetch_json_many",
                lambda ctx, specs, parallel=4: [respond(spec["url"]) for spec in specs])
    return install


@pytest.fixture
def app(tmp_path, monkeypatch):
    from knowitall import server, store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    quits = []
    monkeypatch.setattr(server, "shutdown_hook", lambda: quits.append(1))
    httpd, url = server.serve()
    port = httpd.server_address[1]
    service = httpd.service

    def call(method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = dict(headers or {})
        if host:
            headers["Host"] = host
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, data, response

    page = call("GET", "/")[1].decode()
    token = re.search(r'name="knowitall-token" content="([^"]+)"', page).group(1)

    class App:
        pass

    a = App()
    a.call, a.port, a.token, a.quits, a.page = call, port, token, quits, page
    a.service = service
    a.httpd = httpd
    a.auth = {"X-KnowItAll-Token": token}
    a.json = {"X-KnowItAll-Token": token, "Content-Type": "application/json"}
    yield a
    server.shutdown(httpd)
    conn = getattr(store._local, "conn", None)
    if conn:
        conn.close()
    store._local.conn = None


