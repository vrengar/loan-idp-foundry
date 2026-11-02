"""The UI page and its assets are revalidated on every load, and the page versions each asset by content."""
import hashlib
import os

from fastapi.testclient import TestClient

import app.main as main


def _version(name):
    with open(os.path.join(main._STATIC_DIR, name), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


def test_ui_page_and_static_files_are_revalidated_on_every_load():
    client = TestClient(main.app)
    for path in ("/", "/static/index.html", "/static/app.js", "/static/styles.css"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_page_links_each_asset_with_its_content_version():
    client = TestClient(main.app)
    html = client.get("/").text
    for name in ("app.js", "styles.css"):
        version = _version(name)
        assert f'"/static/{name}?v={version}"' in html, name
        assert client.get(f"/static/{name}?v={version}").status_code == 200, name
