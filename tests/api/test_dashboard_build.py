"""The committed dashboard build is complete and served by the app (CON-06; 23 Oct).

The React sources live in dashboard/; `npm run build` there writes tdea/api/dashboard,
which is committed and shipped as package data so a pip-only install needs no Node.js.
These checks read the build as files and through the Flask test client; the charts
themselves are not exercised here.
"""

from __future__ import annotations

import re
from pathlib import Path

from tdea.api.app import DASHBOARD_DIST, create_app
from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.product_store import ProductStore

ASSET = re.compile(r'(?:src|href)="/(assets/[^"]+)"')


def test_the_build_has_an_index_and_every_asset_it_names() -> None:
    """index.html exists and each asset it references is in the build."""
    index = (DASHBOARD_DIST / "index.html").read_text(encoding="utf-8")
    assets = ASSET.findall(index)
    assert any(name.endswith(".js") for name in assets)
    assert any(name.endswith(".css") for name in assets)
    for name in assets:
        assert (DASHBOARD_DIST / name).is_file(), name
    assert "http://" not in index and "https://" not in index  # nothing loads from the web


def test_the_app_serves_the_build_and_its_client_routes(tmp_path: Path) -> None:
    """The default app serves index.html for client routes and the built assets."""
    app = create_app(AnalysisService(ProductStore(tmp_path / "tdea.db"),
                                     lock_directory=tmp_path / "lock"))
    client = app.test_client()
    index = (DASHBOARD_DIST / "index.html").read_bytes()
    for path in ("/", "/trends?repo=1", "/compare", "/commits"):
        answer = client.get(path)
        assert answer.status_code == 200 and answer.data == index
    for name in ASSET.findall(index.decode("utf-8")):
        answer = client.get("/" + name)
        assert answer.status_code == 200
        assert answer.mimetype in ("text/javascript", "application/javascript", "text/css")
