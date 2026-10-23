"""Selenium acceptance tests of the dashboard: the browser cases of workbook v1.2.

These tests drive the built dashboard (tdea/api/dashboard) in headless Chrome against a
TDEA server on 127.0.0.1. They run only with TDEA_BROWSER_TESTS=1 and are reported as
skipped otherwise. Chrome comes from TDEA_CHROME_BINARY and its driver from
TDEA_CHROMEDRIVER (or CHROMEWEBDRIVER, set on GitHub's runners); without them Selenium
Manager looks for both.

What is real and what is not:

* Real: the Flask application, AnalysisService, ProductEngine with its clones, radon
  and Pylint processes, tdea.db, the dashboard build and the browser.
* FR-03 stubbed transport: the GitHubClient is real, but its urlopen is FakeGitHub,
  which answers GET /repos/{owner}/{repo} as api.github.com would (200 for the fixture
  repositories, 404 for any other, 401 for INVALID_TOKEN). The real request is checked
  by tests/product/test_github_live.py in the "live GitHub API (FR-03)" CI job.
* Each github.com URL is cloned from a local fixture of make_fx_product, so
  https://github.com/psf/requests is FX-S here. No test reaches the network: the
  browser runs with --no-proxy-server and the server listens on loopback only.

The fixtures FX-S, FX-M, FX-RISK, FX-L, FX-UP, FX-DOWN and the comparison pair are
analysed once per session through the service, so the dashboard cases open finished
results. Cases that watch an analysis in the browser use their own repository names
(fx-m-run, fx-l-run, fx-100), so they never change the shared results.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import make_fx_product
import pytest
from browser_support import (ANALYSED, BROKEN, BROWSER_FLAG, PROXY_VARIABLES, RECORDER,
                             RUN_ONLY, CountingSources, Dashboard, FakeGitHub,
                             LiveServer, Tdea, url)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.webdriver import WebDriver

from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.product_store import ProductStore
from tdea.analyzer.product_engine import ProductEngine
from tdea.api.app import create_app
from tdea.git_handler.github_client import GitHubClient


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the browser tests unless TDEA_BROWSER_TESTS=1."""
    del config
    if os.environ.get(BROWSER_FLAG) == "1":
        return
    here = Path(__file__).resolve().parent
    skip = pytest.mark.skip(reason=f"browser test: set {BROWSER_FLAG}=1 to run it")
    for item in items:
        if here in Path(str(item.fspath)).resolve().parents:
            item.add_marker(skip)


@pytest.fixture(name="tdea", scope="session")
def _tdea(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Tdea]:
    """Build the fixtures, start TDEA on loopback and analyse the shared fixtures."""
    root = tmp_path_factory.mktemp("browser")
    built = {key: make_fx_product.BUILDERS[key](root / "fixtures" / key).path
             for key in sorted(set(ANALYSED.values()) | set(RUN_ONLY.values()))}
    names = {**ANALYSED, **RUN_ONLY}
    sources = CountingSources({f"tdea-fixtures/{name}": built[key]
                               for name, key in names.items()})
    sources["psf/requests"] = built["fx-s"]  # TC-002, TC-003, TC-012
    sources[BROKEN] = root / "fixtures" / "missing"
    github = FakeGitHub(set(sources))
    clone_root = root / "clones"
    clone_root.mkdir()
    engine = ProductEngine(client=GitHubClient(urlopen=github),
                           clone_source=lambda repo: sources[repo.key], temp_root=clone_root)
    service = AnalysisService(ProductStore(root / "tdea.db"), engine,
                              lock_directory=root / "lock")
    server = LiveServer(create_app(service))
    tdea = Tdea(service, server.base_url, github, sources, clone_root, root / "downloads",
                built)
    tdea.downloads.mkdir()
    for name in ANALYSED:
        submission = service.submit(url(name))
        assert service.wait(submission.repository_id, 900), name
        tdea.ids[name] = submission.repository_id
    sources.clones.clear()
    github.requests.clear()
    yield tdea
    server.stop()


@pytest.fixture(name="driver", scope="session")
def _driver(tdea: Tdea) -> Iterator[WebDriver]:
    """Start headless Chrome with downloads into the session's download folder."""
    options = webdriver.ChromeOptions()
    for argument in ("--headless=new", "--window-size=1400,1000", "--no-proxy-server",
                     "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"):
        options.add_argument(argument)
    options.add_experimental_option("prefs", {
        "download.default_directory": str(tdea.downloads),
        "download.prompt_for_download": False,
    })
    if os.environ.get("TDEA_CHROME_BINARY"):
        options.binary_location = os.environ["TDEA_CHROME_BINARY"]
    driver_path = os.environ.get("TDEA_CHROMEDRIVER")
    if not driver_path and os.environ.get("CHROMEWEBDRIVER"):
        driver_path = str(Path(os.environ["CHROMEWEBDRIVER"]) / "chromedriver")
    service = Service(executable_path=driver_path) if driver_path else Service()
    clean = {key: value for key, value in os.environ.items() if key not in PROXY_VARIABLES}
    with mock.patch.dict(os.environ, clean, clear=True):
        driver = webdriver.Chrome(options=options, service=service)
    driver.execute_cdp_cmd("Browser.setDownloadBehavior",
                           {"behavior": "allow", "downloadPath": str(tdea.downloads)})
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER})
    yield driver
    driver.quit()


@pytest.fixture(name="dashboard")
def _dashboard(driver: WebDriver, tdea: Tdea) -> Dashboard:
    """Return the page helper, with no analysis running."""
    tdea.wait_idle()
    return Dashboard(driver, tdea)
