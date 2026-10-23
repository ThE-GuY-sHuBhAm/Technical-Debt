"""Workbook v1.2 browser cases of the Research Comparison view: TC-113, TC-115 to TC-119,
TC-121 and TC-138 (FR-44 to FR-47, NFR-05).

The comparison pair fx-cmp-a (10 commits, Sep 2026) and fx-cmp-b (15 commits, Oct 2026)
is analysed once per session; TC-113 analyses its own copies (new-cmp-a, new-cmp-b)
through the view. The workbook names the repositories of TC-113 only as an LLM-assisted
and a human-authored track repository; the generated pair stands in for them.
"""

from __future__ import annotations

import csv
import re
import statistics

from browser_support import Dashboard

COMPARISON = "Debt Score timelines, aligned by commit sequence"
COMPARISON_HEADER = ["commit_sequence", "repo_1_debt_score", "repo_1_delta",
                     "repo_1_classification", "repo_2_debt_score", "repo_2_delta",
                     "repo_2_classification"]


def fixture_url(name: str) -> str:
    """Return the github.com URL of fixture repository ``name``."""
    return f"https://github.com/tdea-fixtures/{name}"


def open_new_pair_form(dashboard: Dashboard) -> None:
    """Open the comparison view and unfold 'Or analyse two new repositories'."""
    dashboard.open("/compare", "main.content details summary")
    dashboard.find("main.content details summary").click()
    dashboard.find("#url-1")


def compare_page(dashboard: Dashboard) -> None:
    """Open the comparison of fx-cmp-a (repository 1) with fx-cmp-b (repository 2)."""
    ids = dashboard.tdea.ids
    dashboard.open(f"/compare?repo_1={ids['fx-cmp-a']}&repo_2={ids['fx-cmp-b']}",
                   f'figure[aria-label="{COMPARISON}"] canvas')


def test_tc113_two_new_urls_start_both_analyses(dashboard: Dashboard) -> None:
    """TC-113: two URL fields; both URLs accepted; both analyses start, then the result."""
    open_new_pair_form(dashboard)
    dashboard.type_into("#url-1", fixture_url("new-cmp-a"))
    dashboard.type_into("#url-2", fixture_url("new-cmp-b"))
    dashboard.click_button("Analyse both")
    dashboard.until(lambda: dashboard.path().startswith("/progress?"),
                    message="no progress page")
    dashboard.until(lambda: len(dashboard.all('[role="progressbar"]')) == 2,
                    message="not two progress bars")
    labels = {item.get_attribute("aria-label") for item in dashboard.all('[role="progressbar"]')}
    assert labels == {"Progress of tdea-fixtures/new-cmp-a",
                      "Progress of tdea-fixtures/new-cmp-b"}
    dashboard.until(lambda: dashboard.path().startswith("/compare?repo_1="), 300,
                    "the comparison did not open")
    dashboard.find(f'figure[aria-label="{COMPARISON}"] canvas')
    statuses = {(item["id"], item["status"]) for item in dashboard.recorded()["responses"]}
    second = dashboard.tdea.repository_id("new-cmp-b")
    assert (second, "Pending") in statuses  # FR-30: waiting while the first runs
    assert dashboard.tdea.sources.clones[-2:] == ["tdea-fixtures/new-cmp-a",
                                                  "tdea-fixtures/new-cmp-b"]


def test_tc115_invalid_second_url_is_reported_next_to_field_2(dashboard: Dashboard) -> None:
    """TC-115: the error is shown under field 2 only, and field 1 keeps its value."""
    open_new_pair_form(dashboard)
    dashboard.type_into("#url-1", fixture_url("fx-s"))
    dashboard.type_into("#url-2", "github.com/psf")
    dashboard.click_button("Analyse both")
    dashboard.find('#url-2[aria-invalid="true"]')
    following = dashboard.driver.execute_script(
        "return document.querySelector('#url-2').nextElementSibling.className;")
    assert following == "error"
    assert dashboard.find("#url-1").get_attribute("aria-invalid") == "false"
    assert dashboard.driver.execute_script(
        "return document.querySelector('#url-1').nextElementSibling.tagName;") == "LABEL"
    assert len(dashboard.all("main.content .error")) == 1
    assert "Invalid repository URL" in dashboard.text("main.content .error")
    assert dashboard.find("#url-1").get_attribute("value") == fixture_url("fx-s")
    assert dashboard.find("#url-2").get_attribute("value") == "github.com/psf"


def test_tc116_history_pair_loads_without_new_analysis(dashboard: Dashboard) -> None:
    """TC-116: choosing two analysed repositories clones and analyses nothing."""
    tdea = dashboard.tdea
    clones, checks = len(tdea.sources.clones), len(tdea.github.requests)
    dashboard.open("/compare", "#repo-1")
    for field, name in (("#repo-1", "fx-cmp-a"), ("#repo-2", "fx-cmp-b")):
        dashboard.find(f'{field} option[value="{tdea.ids[name]}"]').click()
    dashboard.click_button("Compare")
    dashboard.find(f'figure[aria-label="{COMPARISON}"] canvas')
    assert dashboard.path() == f"/compare?repo_1={tdea.ids['fx-cmp-a']}" \
        f"&repo_2={tdea.ids['fx-cmp-b']}"
    assert len(tdea.sources.clones) == clones and len(tdea.github.requests) == checks


def test_tc117_overlay_has_two_coloured_named_lines(dashboard: Dashboard) -> None:
    """TC-117: one chart, two lines in different colours, legend names both."""
    compare_page(dashboard)
    chart = dashboard.chart(COMPARISON)
    assert len(chart["datasets"]) == 2 and chart["type"] == "line"
    assert chart["datasets"][0]["borderColor"] != chart["datasets"][1]["borderColor"]
    assert chart["legend"]["items"] == ["tdea-fixtures/fx-cmp-a", "tdea-fixtures/fx-cmp-b"]
    assert len(dashboard.all("main.content canvas")) == 1


def test_tc118_x_axis_is_commit_sequence(dashboard: Dashboard) -> None:
    """TC-118: x is 1, 2, 3 ...; both lines start at 1; A ends at 10 and B at 15."""
    compare_page(dashboard)
    chart = dashboard.chart(COMPARISON)
    assert chart["labels"] == list(range(1, 16))
    assert chart["axes"]["x"] == "Commit sequence (1 = oldest analysed commit)"
    first, second = (item["data"] for item in chart["datasets"])
    assert first[0] is not None and second[0] is not None
    assert [value is not None for value in first] == [True] * 10 + [False] * 5
    assert all(value is not None for value in second)


def comparison_csv(dashboard: Dashboard) -> list[list[str]]:
    """Download the comparison CSV from the view and return its rows."""
    compare_page(dashboard)
    path, _seconds = dashboard.download("Download comparison CSV", ".csv", 10)
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def test_tc119_comparison_csv_header(dashboard: Dashboard) -> None:
    """TC-119: row 1 of the comparison CSV is the FR-46 header."""
    assert comparison_csv(dashboard)[0] == COMPARISON_HEADER


def test_tc121_summary_panel_matches_the_csv(dashboard: Dashboard) -> None:
    """TC-121: mean, peak, spike and drop counts recalculated from the CSV match the panel."""
    rows = comparison_csv(dashboard)[1:]
    compare_page(dashboard)
    dashboard.find("main.content table tbody tr")
    panel = {row.find_element("css selector", "th").text:
             [cell.text for cell in row.find_elements("css selector", "td")]
             for row in dashboard.all("main.content table tbody tr")}
    for slot in (0, 1):
        scores = [float(row[1 + 3 * slot]) for row in rows if row[1 + 3 * slot]]
        classes = [row[3 + 3 * slot] for row in rows if row[3 + 3 * slot]]
        assert panel["Commits analysed"][slot] == str(len(scores))
        assert panel["Mean Debt Score"][slot] == f"{statistics.fmean(scores):.2f}"
        assert panel["Peak Debt Score"][slot] == f"{max(scores):.2f}"
        assert panel["Debt Spikes"][slot] == str(classes.count("Debt Spike"))
        assert panel["Refactoring Drops"][slot] == str(classes.count("Refactoring Drop"))
        assert re.fullmatch(r"(Increasing|Decreasing|Stable) \(slope [+-]?\d+\.\d{2} per "
                            r"commit\)", panel["Overall trend"][slot])


def test_tc138_five_repositories_without_restart(dashboard: Dashboard) -> None:
    """TC-138: five repositories analysed from Home in one server run; each shows its own.

    They are FX-S, FX-M, FX-RISK, FX-UP and FX-DOWN under their own names (multi-*), so
    the session's shared results are not touched.
    """
    expected = {"multi-s": 12, "multi-m": 50, "multi-risk": 1, "multi-up": 12,
                "multi-down": 12}
    for name in expected:
        dashboard.tdea.wait_idle()
        dashboard.open("/", "#url")
        dashboard.type_into("#url", fixture_url(name))
        dashboard.find('main.content form button[type="submit"]').click()
        dashboard.until(lambda: dashboard.path().startswith("/overview?repo="), 300,
                        f"{name} did not finish")
    for name, commits in expected.items():
        repository_id = dashboard.tdea.repository_id(name)
        dashboard.open(f"/overview?repo={repository_id}",
                       'section[aria-label="Repository overview"]')
        card = dashboard.text('section[aria-label="Repository overview"]')
        assert name in card and "Complete" in card
        stored = dashboard.tdea.service.store.repository(repository_id)
        assert stored.commits_analysed == commits, name
        chart = dashboard.chart("Debt Score timeline")
        assert len(chart["datasets"][0]["data"]) == commits, name
