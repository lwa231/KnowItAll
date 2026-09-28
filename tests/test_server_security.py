import http.client
import json

import pytest

from knowitall import server, store


def test_index_carries_a_real_token_not_the_placeholder(app):
    assert app.token and app.token != server.TOKEN_PLACEHOLDER
    assert server.TOKEN_PLACEHOLDER not in app.page
    assert len(app.token) >= 32


def test_api_get_needs_the_token(app):
    assert app.call("GET", "/api/state")[0] == 403
    assert app.call("GET", "/api/state", headers={"X-KnowItAll-Token": "wrong"})[0] == 403
    status, body, _ = app.call("GET", "/api/state", headers=app.auth)
    assert status == 200 and "companies" in json.loads(body)


def test_every_api_get_route_is_protected(app):
    for route in ("/api/state", "/api/history", "/api/run-jobs?id=1", "/api/output"):
        assert app.call("GET", route)[0] == 403, route
        assert app.call("GET", route, headers=app.auth)[0] == 200, route


def test_api_post_needs_the_token(app):
    status = app.call("POST", "/api/queue", {"urls": []}, {"Content-Type": "application/json"})[0]
    assert status == 403
    status = app.call("POST", "/api/queue", {"urls": []}, {"Content-Type": "application/json",
                                                             "X-KnowItAll-Token": "nope"})[0]
    assert status == 403
    assert app.call("POST", "/api/queue", {"urls": []}, app.json)[0] == 200


def test_unauthenticated_quit_does_nothing(app):
    assert app.call("POST", "/api/quit", {}, {"Content-Type": "application/json"})[0] == 403
    assert app.quits == []
    assert app.call("POST", "/api/quit", {}, app.json)[0] == 200
    import time
    time.sleep(0.2)
    assert app.quits == [1]


def test_post_must_be_json(app):
    """A cross-site page can send text/plain without a preflight; that must not be accepted."""
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        headers = {"X-KnowItAll-Token": app.token, "Content-Type": content_type}
        assert app.call("POST", "/api/queue", '{"urls": []}', headers)[0] == 415, content_type
    headers = {"X-KnowItAll-Token": app.token}                 # no content type at all
    assert app.call("POST", "/api/queue", '{"urls": []}', headers)[0] == 415
    headers = {**app.auth, "Content-Type": "application/json; charset=utf-8"}
    assert app.call("POST", "/api/queue", {"urls": []}, headers)[0] == 200


def test_invalid_json_body_is_a_400_not_silently_empty(app):
    assert app.call("POST", "/api/queue", "{not json", app.json)[0] == 400
    assert app.call("POST", "/api/queue", "[1, 2]", app.json)[0] == 400


def test_foreign_origin_is_rejected_even_with_the_token(app):
    evil = {**app.json, "Origin": "https://evil.example"}
    assert app.call("POST", "/api/queue", {"urls": []}, evil)[0] == 403
    assert app.call("GET", "/api/state", headers={**app.auth, "Origin": "https://evil.example"})[0] == 403
    assert app.call("GET", "/api/state", headers={**app.auth, "Origin": "null"})[0] == 403


def test_our_own_origin_is_accepted(app):
    for origin in (f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"):
        assert app.call("POST", "/api/queue", {"urls": []}, {**app.json, "Origin": origin})[0] == 200
    wrong_port = f"http://127.0.0.1:{app.port + 1}"
    assert app.call("GET", "/api/state", headers={**app.auth, "Origin": wrong_port})[0] == 403


def test_dns_rebinding_host_is_still_rejected(app):
    assert app.call("GET", "/api/state", headers=app.auth, host="evil.example")[0] == 403
    assert app.call("GET", "/", host="evil.example")[0] == 403


def test_static_ui_files_stay_open_and_non_api_post_is_404(app):
    assert app.call("GET", "/")[0] == 200
    assert app.call("POST", "/nowhere", {}, app.json)[0] == 404


def test_rejected_post_does_not_poison_the_connection(app):
    """The rejected request's body must not be read as the next request on a keep-alive socket."""
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    conn.request("POST", "/api/queue", body='{"urls": []}', headers={"Content-Type": "text/plain"})
    first = conn.getresponse()
    first.read()
    assert first.status == 403
    assert first.getheader("Connection") == "close"
    conn.close()


def test_tokens_are_fresh_per_server(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store._local, "conn", None, raising=False)
    first, _ = server.serve()
    token_one = server.TOKEN
    second, _ = server.serve()
    assert server.TOKEN != token_one
    first.shutdown()
    second.shutdown()


def test_security_headers_present(app):
    _, _, response = app.call("GET", "/api/state", headers=app.auth)
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert response.getheader("Cache-Control") == "no-store"
