"""Support for the Selenium acceptance tests in tests/browser (workbook v1.2 browser cases).

FakeGitHub is the FR-03 stubbed transport, CountingSources records clones, Tdea and
LiveServer hold the TDEA server on 127.0.0.1, and Dashboard drives the pages and reads
the charts through the reference charts.jsx leaves on each canvas. tests/browser/
conftest.py builds them; its docstring says what is real and what is stubbed.
"""

from __future__ import annotations

import io
import json
import shutil
import threading
import time
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait
from werkzeug.serving import make_server

from tdea.aggregator.analysis_service import AnalysisService


BROWSER_FLAG = "TDEA_BROWSER_TESTS"
INVALID_TOKEN = "ghp_invalidtoken000000000000000"  # TC-014's token
WAIT = 30.0  # seconds a page may take to show what a test waits for
PROXY_VARIABLES = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                   "ALL_PROXY")

# Repository name -> fixture. The analysed ones are opened by the dashboard cases.
ANALYSED = {"fx-s": "fx-s", "fx-m": "fx-m", "fx-risk": "fx-risk", "fx-l": "fx-l",
            "fx-up": "fx-up", "fx-down": "fx-down", "fx-cmp-a": "fx-cmp-a",
            "fx-cmp-b": "fx-cmp-b"}
RUN_ONLY = {"fx-m-run": "fx-m", "fx-l-run": "fx-l", "fx-100": "fx-100",
            "multi-s": "fx-s", "multi-m": "fx-m", "multi-risk": "fx-risk",
            "multi-up": "fx-up", "multi-down": "fx-down", "new-cmp-a": "fx-cmp-a",
            "new-cmp-b": "fx-cmp-b", "no-token": "fx-up", "bad-token": "fx-up"}
BROKEN = "tdea-fixtures/broken"  # passes FR-03, but its clone fails (TC-087)

# Installed in every page before the dashboard loads: it records each overview answer
# the frontend receives and each value the progress bars show (TC-054, TC-087, TC-098).
RECORDER = r"""
(() => {
  const log = { responses: [], bar: [] };
  window.__tdea = log;
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const response = await realFetch(input, init);
    if (/^\/api\/repositories\/\d+$/.test(String(input))) {
      try {
        const body = await response.clone().json();
        const progress = body.progress || {};
        log.responses.push({ id: body.repository_id, status: body.status,
          percent: progress.percent ?? null, processed: progress.processed ?? null,
          total: progress.total ?? null, sha: progress.current_sha ?? null });
      } catch (error) { /* not JSON: nothing to record */ }
    }
    return response;
  };
  const seen = new Map();
  const look = () => document.querySelectorAll('[role=progressbar]').forEach((bar) => {
    const key = bar.getAttribute('aria-label');
    const value = Number(bar.getAttribute('aria-valuenow'));
    const text = bar.parentElement ? bar.parentElement.innerText : '';
    if (seen.get(key) !== `${value}|${text}`) {
      seen.set(key, `${value}|${text}`);
      log.bar.push({ key, value, text });
    }
  });
  new MutationObserver(look).observe(document,
    { attributes: true, childList: true, subtree: true, characterData: true });
})();
"""


def url(name: str) -> str:
    """Return the github.com URL of fixture repository ``name``."""
    return f"https://github.com/tdea-fixtures/{name}"


class FakeGitHub:
    """FR-03 stubbed transport: answers GET /repos/{owner}/{repo} like api.github.com."""

    def __init__(self, public: set[str]) -> None:
        """Answer 200 for the ``public`` owner/name keys and 404 for any other."""
        self.public = public
        self.requests: list[tuple[str, str | None]] = []

    def __call__(self, request: Any, timeout: float, context: Any) -> io.BytesIO:
        """Record the request (repository key, Authorization header) and answer it."""
        del timeout, context
        key = request.full_url.split("/repos/", 1)[1].lower()
        authorization = request.get_header("Authorization")
        self.requests.append((key, authorization))
        if authorization == f"Bearer {INVALID_TOKEN}":
            raise _http_error(request.full_url, 401)
        if key not in self.public:
            raise _http_error(request.full_url, 404)
        body = {"full_name": key, "private": False, "size": 42, "default_branch": "main"}
        return io.BytesIO(json.dumps(body).encode())

    def keys(self) -> list[str]:
        """Return the repository keys checked so far."""
        return [key for key, _authorization in self.requests]


def _http_error(address: str, status: int) -> urllib.error.HTTPError:
    """Return the HTTPError urllib raises for ``status``."""
    return urllib.error.HTTPError(address, status, "error", Message(), io.BytesIO(b"{}"))


class CountingSources(dict):
    """The local fixture behind each repository key; records every clone (TC-004, TC-116)."""

    def __init__(self, sources: dict[str, Path]) -> None:
        """Serve ``sources``."""
        super().__init__(sources)
        self.clones: list[str] = []

    def __getitem__(self, key: str) -> Path:
        """Record the clone, then return the fixture path."""
        self.clones.append(key)
        return super().__getitem__(key)


@dataclass
class Tdea:
    """The running TDEA server and what the tests need to know about it."""

    service: AnalysisService
    base_url: str
    github: FakeGitHub
    sources: CountingSources
    clone_root: Path
    downloads: Path
    fixtures: dict[str, Path]
    ids: dict[str, int] = field(default_factory=dict)

    def repository_id(self, name: str) -> int:
        """Return the repository_id of fixture repository ``name``, if it is stored."""
        found = self.service.store.find(f"tdea-fixtures/{name}")
        assert found is not None, name
        return found.repository_id

    def wait_idle(self, timeout: float = 600) -> None:
        """Wait until no analysis runs, so a test starts from a quiet server."""
        deadline = time.monotonic() + timeout
        for row in self.service.store.repositories():
            remaining = max(0.0, deadline - time.monotonic())
            assert self.service.wait(row.repository_id, remaining), row.full_name_key


class LiveServer:
    """A werkzeug server for one Flask application on 127.0.0.1, in a daemon thread."""

    def __init__(self, app: Any) -> None:
        """Bind to a free loopback port and start serving."""
        self._server = make_server("127.0.0.1", 0, app, threaded=True)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop serving."""
        self._server.shutdown()
        self._thread.join(10)


class Dashboard:
    """Page actions and reads shared by the browser tests."""

    def __init__(self, driver: WebDriver, tdea: Tdea) -> None:
        """Drive ``driver`` against the ``tdea`` server."""
        self.driver = driver
        self.tdea = tdea

    def open(self, path: str = "/", ready: str | None = None) -> None:
        """Load ``path`` and wait for the CSS selector ``ready`` (default: the page shell)."""
        self.driver.get(self.tdea.base_url + path)
        self.find(ready or "main.content")

    def find(self, selector: str, timeout: float = WAIT) -> WebElement:
        """Wait for one visible element matching ``selector`` and return it."""
        return WebDriverWait(self.driver, timeout).until(
            lambda driver: next((element for element in driver.find_elements(
                By.CSS_SELECTOR, selector) if element.is_displayed()), False),
            message=f"no visible {selector!r}")

    def all(self, selector: str) -> list[WebElement]:
        """Return every element matching ``selector`` now."""
        return self.driver.find_elements(By.CSS_SELECTOR, selector)

    def until(self, condition: Callable[[], Any], timeout: float = WAIT,
              message: str = "") -> Any:
        """Wait until ``condition()`` is truthy and return its value."""
        return WebDriverWait(self.driver, timeout, poll_frequency=0.05).until(
            lambda _driver: condition(), message=message)

    def text(self, selector: str = "main.content") -> str:
        """Return the visible text of the first element matching ``selector``."""
        return self.find(selector).text

    def path(self) -> str:
        """Return the page's path and query."""
        return self.driver.execute_script("return location.pathname + location.search;")

    def type_into(self, selector: str, value: str) -> None:
        """Replace the value of the input ``selector`` by typing ``value``."""
        element = self.find(selector)
        element.clear()
        element.send_keys(value)

    def click_button(self, label: str, scope: str = "main.content") -> None:
        """Click the button or link in ``scope`` whose text is ``label``."""
        element = self.until(lambda: next((item for item in self.all(
            f"{scope} button, {scope} a") if item.is_displayed()
            and item.text.strip() == label), None), message=f"no {label!r} control")
        element.click()

    def chart(self, title: str, timeout: float = WAIT) -> dict[str, Any]:
        """Return the data, colours, annotations and point positions of chart ``title``."""
        self.find(f'figure[aria-label="{title}"] canvas')
        return self.until(lambda: self.driver.execute_script(_CHART_JS, title), timeout,
                          f"chart {title!r} not drawn")

    def hover_point(self, title: str, index: int, dataset: int = 0) -> dict[str, Any]:
        """Move the mouse onto point ``index`` of chart ``title``; return its tooltip."""
        self._pointer(title, index, dataset, click=False)
        return self.until(lambda: self.driver.execute_script(_TOOLTIP_JS, title),
                          message=f"no tooltip on {title!r}")

    def click_point(self, title: str, index: int, dataset: int = 0) -> None:
        """Click point ``index`` of chart ``title``."""
        self._pointer(title, index, dataset, click=True)

    def _pointer(self, title: str, index: int, dataset: int, click: bool) -> None:
        """Dispatch real pointer events at a point's pixel position (CDP Input)."""
        chart = self.chart(title)
        point = chart["points"][dataset][index]
        assert point is not None, f"point {index} of {title!r} is not drawn"
        self.driver.execute_script(
            "document.querySelector(`figure[aria-label=\"${arguments[0]}\"]`)"
            ".scrollIntoView({block: 'center'});", title)
        rect = self.driver.execute_script(
            "const r = document.querySelector(`figure[aria-label=\"${arguments[0]}\"] canvas`)"
            ".getBoundingClientRect(); return {x: r.left, y: r.top};", title)
        x, y = rect["x"] + point["x"], rect["y"] + point["y"]
        self.driver.execute_cdp_cmd("Input.dispatchMouseEvent",
                                    {"type": "mouseMoved", "x": x, "y": y})
        if click:
            for kind in ("mousePressed", "mouseReleased"):
                self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                    "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    def recorded(self) -> dict[str, list[dict[str, Any]]]:
        """Return what the recorder saw on this page: overview answers and bar values."""
        return self.driver.execute_script("return window.__tdea;")

    def download(self, label: str, suffix: str, timeout: float) -> tuple[Path, float]:
        """Click the download ``label``; return the file and seconds until it was complete."""
        for old in self.tdea.downloads.iterdir():
            if old.is_file():
                old.unlink()
            else:
                shutil.rmtree(old)
        started = time.monotonic()
        self.click_button(label)
        deadline = started + timeout + 20
        while time.monotonic() < deadline:
            done = [item for item in self.tdea.downloads.iterdir()
                    if item.suffix == suffix and item.stat().st_size > 0]
            if done:
                return done[0], time.monotonic() - started
            time.sleep(0.05)
        raise TimeoutException(f"{label}: no {suffix} file in {timeout + 20} s")


_CHART_JS = r"""
const figure = document.querySelector(`figure[aria-label="${arguments[0]}"]`);
const chart = figure && figure.querySelector('canvas').tdeaChart;
if (!chart || !chart.chartArea) return null;
const legend = chart.options.plugins.legend;
return {
  type: chart.config.type,
  labels: chart.data.labels,
  datasets: chart.data.datasets.map((set) => ({
    label: set.label, data: set.data, borderColor: set.borderColor ?? null,
    backgroundColor: set.backgroundColor ?? null, pointStyle: set.pointStyle ?? null,
    rotation: set.rotation ?? null, showLine: set.showLine ?? null })),
  points: chart.data.datasets.map((_set, index) => chart.getDatasetMeta(index).data.map(
    (element) => (element && !element.skip && Number.isFinite(element.x)
      && Number.isFinite(element.y) ? { x: element.x, y: element.y } : null))),
  legend: { display: legend.display !== false,
    items: (chart.legend && chart.legend.legendItems || []).map((item) => item.text) },
  axes: { x: chart.options.scales.x.title.text, y: chart.options.scales.y.title.text,
    yMin: chart.scales.y.min, yMax: chart.scales.y.max,
    stacked: Boolean(chart.options.scales.y.stacked) },
  title: chart.options.plugins.title.text,
  annotations: JSON.parse(JSON.stringify(
    (chart.options.plugins.annotation || {}).annotations || {})),
  area: { top: chart.chartArea.top, bottom: chart.chartArea.bottom,
    left: chart.chartArea.left, right: chart.chartArea.right },
};
"""

_TOOLTIP_JS = r"""
const figure = document.querySelector(`figure[aria-label="${arguments[0]}"]`);
const tip = figure.querySelector('canvas').tdeaChart.tooltip;
if (!tip || tip.opacity === 0 || !tip.dataPoints || !tip.dataPoints.length) return null;
return { title: tip.title, body: tip.body.map((part) => part.lines).flat(),
  afterBody: tip.afterBody, footer: tip.footer,
  indices: tip.dataPoints.map((point) => point.dataIndex),
  datasets: tip.dataPoints.map((point) => point.datasetIndex) };
"""
