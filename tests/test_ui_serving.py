import http.client

import pytest


def fetch(app, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, body, response


@pytest.mark.parametrize("path, kind", [
    ("/ui/js/main.js", "javascript"), ("/ui/js/views/system.js", "javascript"), ("/ui/css/tokens.css", "text/css"),
    ("/ui/css/fonts.css", "text/css"), ("/ui/fonts/inter-latin.woff2", "font/woff2"),
    ("/ui/fonts/fira-code-latin.woff2", "font/woff2"),
])
def test_static_files_are_served_without_a_token_with_the_right_type(app, path, kind):
    status, body, response = fetch(app, path)
    assert status == 200 and body
    assert kind in response.getheader("Content-Type")
    assert response.getheader("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize("path", [
    "/ui/../knowitall/store.py", "/ui/%2e%2e/knowitall/store.py", "/ui/..%2fknowitall/store.py",
    "/ui/css/../../knowitall/paths.py", "/ui//etc/passwd", "/ui/", "/ui/js", "/ui/nothing.js",
])
def test_static_route_cannot_escape_the_ui_folder(app, path):
    status, body, _ = fetch(app, path)
    assert status == 404
    assert b"import " not in body and b"root:" not in body


def test_index_gets_the_saved_theme_and_a_token_and_no_placeholders(app, tmp_path):
    app.service.settings_path = tmp_path / "settings.json"
    status, body, _ = fetch(app, "/")
    page = body.decode()
    assert status == 200 and 'data-theme="dark"' in page
    assert "__KNOWITALL_" not in page and app.token in page
    app.service.update_settings({"theme": "light"})
    assert 'data-theme="light"' in fetch(app, "/")[1].decode()


def test_the_page_never_reveals_the_token_to_other_files(app):
    for path in ("/ui/js/main.js", "/ui/css/tokens.css"):
        assert app.token.encode() not in fetch(app, path)[1]
