"""Opt-in: drives a real headless Chrome. Run with:  pytest -m integration"""
import http.server
import threading

import pytest

from knowitall.browser import BrowserPool

pytestmark = pytest.mark.integration

PAGE = ("<html><head><title>Jobs</title></head><body><div id='out'></div><script>"
        "document.getElementById('out').innerHTML = '<a href=\"/job/1\">Rendered by JS engineer</a>';"
        "</script><p>" + "filler " * 120 + "</p></body></html>")


@pytest.fixture
def local_site():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE.encode())

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


def test_real_chrome_renders_javascript_and_closes(local_site):
    pool = BrowserPool(size=1, should_cancel=lambda: False)
    try:
        page = pool.render(local_site)
        assert page["error"] is None, page
        assert page["status"] == 200
        assert "Rendered by JS engineer" in page["html"]        # only present after the script ran
        assert pool.active_drivers() == 1
        again = pool.render(local_site)                          # second render reuses the same Chrome
        assert again["status"] == 200 and pool.active_drivers() == 1
    finally:
        pool.close()
    assert pool.active_drivers() == 0
