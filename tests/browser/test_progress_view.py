"""Workbook v1.2 browser cases of a running analysis: TC-033, TC-052 to TC-054, TC-087,
TC-098 and TC-099 (FR-17, FR-18, FR-30, FR-38, SRS 4.1 Cancel).

Each test submits from the Home page and watches the Analysis Progress view. The page
recorder (conftest.RECORDER) keeps every overview answer the frontend receives and every
value the progress bar shows.

TC-033 expects the status to read 'Cancelled'. FR-30 allows only Pending, Running,
Complete and Error, so TDEA shows Error with the error type "Analysis cancelled"; that
part is a conflict between the workbook and the SRS, which the test records rather than
passes. In TC-087 a single submission is Pending only until its thread starts, which is
too short for a 1-second poll to see; Pending is seen in the browser for the second
repository of a pair (test_comparison_view.py, TC-113).
"""

from __future__ import annotations

import re
import time

from browser_support import BROKEN, Dashboard

SHA = re.compile(r"[0-9a-f]{10}")


def submit(dashboard: Dashboard, address: str, refresh: bool = False) -> float:
    """Submit ``address`` from Home; return the monotonic time of the click."""
    dashboard.open("/", "#url")
    dashboard.type_into("#url", address)
    if refresh:
        dashboard.find('main.content input[type="checkbox"]').click()
    button = dashboard.find('main.content form button[type="submit"]')
    clicked = time.monotonic()
    button.click()
    return clicked


def finished(dashboard: Dashboard, timeout: float = 600) -> None:
    """Wait until the progress view has handed over to the repository's overview."""
    dashboard.until(lambda: dashboard.path().startswith("/overview?repo="), timeout,
                    "the analysis did not finish")
    dashboard.find('section[aria-label="Repository overview"]')


def fixture_url(name: str) -> str:
    """Return the github.com URL of fixture repository ``name``."""
    return f"https://github.com/tdea-fixtures/{name}"


def test_tc052_tc053_fx_l_runs_in_the_background(dashboard: Dashboard) -> None:
    """TC-052: the progress page within 2 s. TC-053: other pages work during the run."""
    tdea = dashboard.tdea
    clicked = submit(dashboard, fixture_url("fx-l-run"))
    dashboard.find('[role="progressbar"]', timeout=10)
    shown = time.monotonic() - clicked
    assert shown <= 2, f"TC-052: {shown:.2f} s"
    progress_path = dashboard.path()
    repository_id = tdea.repository_id("fx-l-run")
    dashboard.until(lambda: (tdea.service.progress(repository_id) or None)
                    and tdea.service.progress(repository_id).processed > 0, 120,
                    "the analysis did not start")
    before = tdea.service.progress(repository_id).processed
    dashboard.open("/", "main.content table tbody tr")  # TC-053: the history
    row = next(item for item in dashboard.all("main.content table tbody tr")
               if "tdea-fixtures/fx-l-run" in item.text)
    assert "Running" in row.text and "Show progress" in row.text
    dashboard.open(f"/overview?repo={tdea.ids['fx-s']}",
                   'section[aria-label="Repository overview"]')
    dashboard.open(f"/commits?repo={tdea.ids['fx-s']}", "main.content table tbody tr")
    assert len(dashboard.all("main.content table tbody tr")) == 12
    assert not dashboard.all("main.content .error")
    during = tdea.service.progress(repository_id)
    assert during is not None and during.processed >= before  # still running
    dashboard.open(progress_path, '[role="progressbar"]')
    finished(dashboard)
    stored = tdea.service.store.repository(repository_id)
    assert stored.status == "Complete" and stored.commits_analysed == 101


def test_tc054_tc098_fx_100_progress_in_the_browser(dashboard: Dashboard) -> None:
    """TC-054: >= 10 integer, never-decreasing values ending at 100. TC-098: % and hash.

    TC-054 counts the percentages in the overview answers the frontend received while
    the run went on, and the value the bar shows at the end. TC-098 reads the bar's text
    and checks that no more than 10 commits pass between two answers.
    """
    submit(dashboard, fixture_url("fx-100"))
    finished(dashboard)
    record = dashboard.recorded()
    received = [item for item in record["responses"] if item["percent"] is not None]
    percents = [item["percent"] for item in received]
    assert all(isinstance(value, int) for value in percents)
    assert percents == sorted(percents)
    assert len(set(percents)) >= 10, percents
    shown = [item for item in record["bar"] if item["key"] == "Progress of tdea-fixtures/fx-100"]
    assert shown[-1]["value"] == 100 and "Analysis complete" in shown[-1]["text"]
    assert [item["value"] for item in shown] == sorted(item["value"] for item in shown)
    running = [item for item in shown if "analysed:" in item["text"]]
    assert running, shown
    for item in running:
        assert f"{item['value']}%" in item["text"] and SHA.search(item["text"])
    processed = [item["processed"] for item in received]
    assert all(later - earlier <= 10 for earlier, later in zip(processed, processed[1:])), \
        processed


def test_tc099_fx_s_bar_reaches_100_then_the_dashboard(dashboard: Dashboard) -> None:
    """TC-099: the bar reaches 100 % and the results dashboard is displayed."""
    submit(dashboard, "https://github.com/psf/requests", refresh=True)
    finished(dashboard)
    shown = dashboard.recorded()["bar"]
    assert shown[-1]["value"] == 100 and "Analysis complete" in shown[-1]["text"]
    dashboard.chart("Debt Score timeline")
    assert "Complete" in dashboard.text('section[aria-label="Repository overview"]')


def test_tc087_status_running_complete_and_error(dashboard: Dashboard) -> None:
    """TC-087: FX-M moves Running -> Complete; a failing repository shows 'Error'."""
    tdea = dashboard.tdea
    submit(dashboard, fixture_url("fx-m-run"))
    finished(dashboard)
    statuses = [item["status"] for item in dashboard.recorded()["responses"]
                if item["id"] == tdea.repository_id("fx-m-run")]
    order = ["Pending", "Running", "Complete"]
    assert "Running" in statuses and statuses[-1] == "Complete"
    assert [order.index(item) for item in statuses] == sorted(
        order.index(item) for item in statuses)
    assert "Complete" in dashboard.text('section[aria-label="Repository overview"]')
    submit(dashboard, f"https://github.com/{BROKEN}")
    dashboard.until(lambda: "Analysis stopped" in dashboard.text(), 120, "no Error shown")
    repository_id = tdea.service.store.find(BROKEN).repository_id
    dashboard.open(f"/overview?repo={repository_id}", 'section[aria-label="Repository overview"]')
    assert "Error" in dashboard.text('section[aria-label="Repository overview"]')
    assert dashboard.all("main.content .error")


def test_tc033_cancel_at_30_percent_removes_the_clone(dashboard: Dashboard) -> None:
    """TC-033 (partly): Cancel at ~30 % returns to Home and leaves no temporary clone.

    The status is Error with the error type "Analysis cancelled", not the workbook's
    'Cancelled' (see the module text).
    """
    tdea = dashboard.tdea
    submit(dashboard, fixture_url("fx-m-run"), refresh=True)
    progress = dashboard.find('[role="progressbar"]')
    dashboard.until(lambda: int(progress.get_attribute("aria-valuenow")) >= 30, 120,
                    "the bar did not reach 30 %")
    dashboard.click_button("Cancel")
    dashboard.until(lambda: dashboard.path() == "/", 30, "Cancel did not return to Home")
    tdea.wait_idle()
    assert not list(tdea.clone_root.iterdir())
    stored = tdea.service.store.find("tdea-fixtures/fx-m-run")
    assert (stored.status, stored.error_type) == ("Error", "Analysis cancelled")
    dashboard.open("/", "main.content table tbody tr")
    row = next(item for item in dashboard.all("main.content table tbody tr")
               if "tdea-fixtures/fx-m-run" in item.text)
    assert "Error" in row.text
