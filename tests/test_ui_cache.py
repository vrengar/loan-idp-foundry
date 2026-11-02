"""The UI page and its static files send Cache-Control: no-cache, so a new deploy reaches open browsers."""
from fastapi.testclient import TestClient

from app.main import app


def test_ui_page_and_static_files_are_revalidated_on_every_load():
    client = TestClient(app)
    for path in ("/", "/static/index.html", "/static/app.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path
