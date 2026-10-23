"""Workbook v1.2 browser cases of the results dashboard: TC-086, TC-088 to TC-097,
TC-100 and TC-101 (FR-30 to FR-39, NFR-10).

FX-S, FX-M and FX-RISK are analysed once per session (conftest). Chart values are read
through the Chart.js instance on each canvas and compared with tdea.db; hovers and
clicks are real mouse events at the drawn point's position.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import urllib.request

import pytest
from browser_support import Dashboard, Tdea

LOC = "Lines of code over the commit history"
COMPLEXITY = "Cyclomatic complexity over the commit history"
SMELLS = "Code smells over the commit history"
MI = "Maintainability Index over the commit history"
DEBT = "Debt Score timeline"
COMMIT_AXIS = "Commit sequence (1 = oldest analysed commit)"
SECTIONS = {"Repository Overview": "/overview", "Metric Trends": "/trends",
            "High-Risk Files": "/high-risk", "Commit History": "/commits",
            "Export": "/export"}


def rows(tdea: Tdea, query: str, *parameters: object) -> list[tuple]:
    """Run one query on the session's tdea.db."""
    with sqlite3.connect(tdea.service.store.path) as connection:
        return connection.execute(query, parameters).fetchall()


def aggregates(tdea: Tdea, name: str) -> list[sqlite3.Row]:
    """Return the commits and aggregate metrics of fixture ``name``, oldest first."""
    with sqlite3.connect(tdea.service.store.path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM commits c JOIN aggregate_metrics a USING (commit_id) "
            "WHERE c.repository_id = ? ORDER BY c.sequence", (tdea.ids[name],)).fetchall()


def utc(iso: str) -> str:
    """Format a stored UTC time as the dashboard does: 2026-09-10 13:00 UTC."""
    return f"{iso[:10]} {iso[11:16]} UTC"


def page(name: str, section: str, tdea: Tdea, **extra: str) -> str:
    """Return the path of ``section`` for fixture ``name``."""
    query = "".join(f"&{key}={value}" for key, value in extra.items())
    return f"{section}?repo={tdea.ids[name]}{query}"


def test_tc086_overview_card_of_fx_s(dashboard: Dashboard) -> None:
    """TC-086: name, owner, URL, 12 commits analysed, status Complete, last analysed."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-s", "/overview", tdea), 'section[aria-label="Repository overview"]')
    card = dashboard.find('section[aria-label="Repository overview"]')
    facts = dict(zip([item.text for item in card.find_elements("css selector", "dt")],
                     [item.text for item in card.find_elements("css selector", "dd")]))
    assert card.find_element("css selector", "h1").text == "fx-s"
    assert "Owner tdea-fixtures" in card.text
    assert card.find_element("css selector", "a").get_attribute("href") == \
        "https://github.com/tdea-fixtures/fx-s"
    assert facts["Commits analysed"].startswith("12") and facts["Status"] == "Complete"
    stored = tdea.service.store.repository(tdea.ids["fx-s"]).last_completed_at
    assert facts["Last completed analysis"] == utc(stored)


def test_tc088_loc_chart_has_12_points_of_sloc(dashboard: Dashboard) -> None:
    """TC-088: a line chart of SLOC against commit sequence with 12 drawn points."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-s", "/trends", tdea))
    chart = dashboard.chart(LOC)
    assert chart["type"] == "line" and chart["labels"] == list(range(1, 13))
    assert chart["axes"]["x"] == COMMIT_AXIS
    assert chart["axes"]["y"] == "Source lines of code (SLOC)"
    assert chart["datasets"][0]["data"] == [row["total_sloc"] for row in aggregates(tdea, "fx-s")]
    assert len([point for point in chart["points"][0] if point]) == 12


def test_tc089_loc_tooltip_matches_csv_row_6(dashboard: Dashboard) -> None:
    """TC-089: point #6 shows its short hash, author, time and SLOC, as CSV row #6 does."""
    tdea = dashboard.tdea
    with urllib.request.urlopen(f"{tdea.base_url}/api/repositories/{tdea.ids['fx-s']}"
                                "/export.csv", timeout=30) as answer:
        row = list(csv.DictReader(io.StringIO(answer.read().decode("utf-8"))))[5]
    dashboard.open(page("fx-s", "/trends", tdea))
    tooltip = dashboard.hover_point(LOC, 5)
    assert tooltip["indices"][0] == 5 and row["commit_sequence"] == "6"
    assert tooltip["title"] == [f"Commit 6 · {row['commit_hash'][:10]}"]
    assert tooltip["body"] == [f"Total SLOC: {row['SLOC']}"]
    assert tooltip["afterBody"] == [f"Author: {row['author']}",
                                    f"Date: {utc(row['timestamp'])}"]


def test_tc090_complexity_chart_has_the_cc_5_line(dashboard: Dashboard) -> None:
    """TC-090: mean CC per commit, and a labelled horizontal line at y = 5."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-s", "/trends", tdea))
    chart = dashboard.chart(COMPLEXITY)
    assert chart["datasets"][0]["data"] == [row["mean_cc"] for row in aggregates(tdea, "fx-s")]
    line = chart["annotations"]["rankA"]
    assert (line["type"], line["yMin"], line["yMax"]) == ("line", 5, 5)
    assert line["label"]["display"] and line["label"]["content"] == \
        "CC = 5 (upper bound of rank A)"


def test_tc091_smell_chart_stacks_four_categories_to_the_total(dashboard: Dashboard) -> None:
    """TC-091: four coloured series with a legend; each stack equals total_smells."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-m", "/trends", tdea))
    chart = dashboard.chart(SMELLS)
    labels = ["Convention (C)", "Refactor (R)", "Warning (W)", "Error (E)"]
    assert chart["type"] == "bar" and chart["axes"]["stacked"]
    assert [item["label"] for item in chart["datasets"]] == labels
    assert len({item["backgroundColor"] for item in chart["datasets"]}) == 4
    assert chart["legend"]["display"] and chart["legend"]["items"] == labels
    stored = aggregates(tdea, "fx-m")
    stacks = [sum(values) for values in zip(*(item["data"] for item in chart["datasets"]))]
    assert stacks == [row["total_smells"] for row in stored] and len(stacks) == 50


def _rgb(colour: str) -> tuple[int, int, int]:
    """Return the red, green and blue parts of an rgba() colour."""
    red, green, blue = (int(part) for part in re.findall(r"\d+", colour)[:3])
    return red, green, blue


def test_tc092_mi_chart_shades_below_25_in_red(dashboard: Dashboard) -> None:
    """TC-092: a shaded red band covers MI < 25."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-m", "/trends", tdea))
    chart = dashboard.chart(MI)
    zone = chart["annotations"]["unmaintainable"]
    assert (zone["type"], zone["yMin"], zone["yMax"]) == ("box", 0, 25)
    red, green, blue = _rgb(zone["backgroundColor"])
    assert red > 2 * green and red > 2 * blue
    assert (chart["axes"]["yMin"], chart["axes"]["yMax"]) == (0, 100)
    assert zone["label"]["content"] == "Unmaintainable (MI < 25)"


def test_tc093_debt_timeline_marks_spikes_drops_and_stable(dashboard: Dashboard) -> None:
    """TC-093: one point per commit; Spike, Drop and Stable markers differ and are named."""
    tdea = dashboard.tdea
    stored = aggregates(tdea, "fx-m")
    dashboard.open(page("fx-m", "/overview", tdea))
    chart = dashboard.chart(DEBT)
    line, spike, drop, stable = chart["datasets"]
    assert line["data"] == [row["debt_score"] for row in stored] and len(line["data"]) == 50
    assert chart["legend"]["items"] == ["Debt Score", "Debt Spike (Δ > +5)",
                                        "Refactoring Drop (Δ < −5)", "Stable"]
    assert (spike["pointStyle"], spike["rotation"]) == ("triangle", 0)
    assert (drop["pointStyle"], drop["rotation"]) == ("triangle", 180)
    assert stable["pointStyle"] == "circle"
    assert len({spike["backgroundColor"], drop["backgroundColor"],
                stable["backgroundColor"]}) == 3
    markers = {"Debt Spike": spike, "Refactoring Drop": drop, "Stable": stable}
    for index, row in enumerate(stored):
        assert [name for name, item in markers.items()
                if item["data"][index] is not None] == [row["classification"]]
    for name in markers:
        index = next(i for i, row in enumerate(stored) if row["classification"] == name)
        tooltip = dashboard.hover_point(DEBT, index)
        assert tooltip["indices"][0] == index
        assert f"Classification: {name}" in tooltip["body"], name


def header_buttons(dashboard: Dashboard) -> list:
    """Return the sort buttons of the high-risk table, left to right."""
    return dashboard.all("main.content table thead th button.sort")


def test_tc094_high_risk_table_columns_and_10_rows(dashboard: Dashboard) -> None:
    """TC-094: file path, mean CC, mean smell density, mean MI, risk class; 10 rows."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-risk", "/high-risk", tdea), "main.content table tbody tr")
    headings = [item.text.rstrip("↕▲▼ ").strip() for item in header_buttons(dashboard)]
    for wanted in ("File path", "Mean CC", "Mean smell density", "Mean MI",
                   "Risk classification"):
        assert wanted in headings
    assert len(dashboard.all("main.content table tbody tr")) == 10


_ORDERED = r"""
const [column, descending] = arguments;
const cells = [...document.querySelectorAll('main.content table tbody tr')]
  .map((row) => row.children[column].innerText.trim());
const rank = { High: 3, Medium: 2, Low: 1 };
const value = (text) => (text in rank ? rank[text]
  : (/^-?\d+(\.\d+)?$/.test(text) ? Number(text) : text));
const order = (a, b) => (typeof a === 'string' ? a.localeCompare(b) : a - b);
return cells.every((text, i) => i === 0
  || (descending ? -1 : 1) * order(value(cells[i - 1]), value(text)) <= 0);
"""


@pytest.mark.parametrize("column", range(7))
def test_tc095_first_click_ascending_second_descending(dashboard: Dashboard,
                                                       column: int) -> None:
    """TC-095: for every column, one click sorts ascending and the next descending."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-risk", "/high-risk", tdea), "main.content table tbody tr")
    for clicks, (sort, descending) in enumerate([("ascending", False),
                                                 ("descending", True)], start=1):
        header_buttons(dashboard)[column].click()
        heading = dashboard.all("main.content table thead th")[column]
        assert heading.get_attribute("aria-sort") == sort, clicks
        assert dashboard.driver.execute_script(_ORDERED, column, descending), clicks


DRILL_HEADINGS = ["File path", "LOC", "SLOC", "Mean CC", "Max CC", "CC rank", "MI", "C",
                  "R", "W", "E"]


def drill_down(dashboard: Dashboard) -> list[list[str]]:
    """Return the drill-down table's rows, once it is shown."""
    dashboard.find('section[aria-label="Commit drill-down"] table tbody tr')
    headings = [item.text for item in dashboard.all(
        'section[aria-label="Commit drill-down"] table thead th')]
    assert headings == DRILL_HEADINGS
    return [[cell.text for cell in row.find_elements("css selector", "td")]
            for row in dashboard.all('section[aria-label="Commit drill-down"] table tbody tr')]


def stored_files(tdea: Tdea, name: str, sequence: int) -> list[list[str]]:
    """Return file_metrics of commit ``sequence`` as the drill-down table shows them."""
    found = rows(tdea, "SELECT f.path, f.loc, f.sloc, f.mean_cc, f.max_cc, f.cc_rank, f.mi, "
                 "f.convention, f.refactor, f.warning, f.error FROM file_metrics f JOIN commits "
                 "c USING (commit_id) WHERE c.repository_id = ? AND c.sequence = ? "
                 "ORDER BY f.path", tdea.ids[name], sequence)
    return [[path, str(loc), str(sloc), f"{mean_cc:.2f}", str(max_cc), rank, str(mi),
             *(str(count) for count in counts)]
            for path, loc, sloc, mean_cc, max_cc, rank, mi, *counts in found]


def test_tc096_clicking_loc_point_6_opens_its_files(dashboard: Dashboard) -> None:
    """TC-096: every file at commit #6 with path, LOC, SLOC, CC and grade, MI, C/R/W/E."""
    tdea = dashboard.tdea
    sha = aggregates(tdea, "fx-s")[5]["sha"]
    dashboard.open(page("fx-s", "/trends", tdea))
    dashboard.click_point(LOC, 5)
    dashboard.until(lambda: f"sha={sha}" in dashboard.path(), message="no drill-down")
    assert sorted(drill_down(dashboard)) == stored_files(tdea, "fx-s", 6)


@pytest.mark.parametrize("section, title", [("/trends", MI), ("/overview", DEBT)])
def test_tc097_same_breakdown_from_mi_and_debt_charts(dashboard: Dashboard, section: str,
                                                      title: str) -> None:
    """TC-097: commit #6 opens the same breakdown from each chart; it matches the DB."""
    tdea = dashboard.tdea
    sha = aggregates(tdea, "fx-s")[5]["sha"]
    dashboard.open(page("fx-s", section, tdea))
    dashboard.click_point(title, 5)
    dashboard.until(lambda: f"sha={sha}" in dashboard.path(), message="no drill-down")
    assert sorted(drill_down(dashboard)) == stored_files(tdea, "fx-s", 6)


def test_tc100_every_section_links_to_every_other(dashboard: Dashboard) -> None:
    """TC-100: the five sections are always in the navigation bar and every link works."""
    tdea = dashboard.tdea
    for start, start_path in SECTIONS.items():
        for target, target_path in SECTIONS.items():
            dashboard.open(page("fx-s", start_path, tdea), 'nav[aria-label="Dashboard sections"]')
            links = {item.text: item for item in dashboard.all(
                'nav[aria-label="Dashboard sections"] a')}
            assert set(SECTIONS) <= set(links), start
            links[target].click()
            dashboard.until(lambda path=target_path: dashboard.path().startswith(path),
                            message=f"{start} -> {target}")
            current = dashboard.find('nav[aria-label="Dashboard sections"] a[aria-current="page"]')
            assert current.text == target
            assert f"repo={tdea.ids['fx-s']}" in dashboard.path()
            assert not dashboard.all("main.content .error")


def test_tc101_commit_history_lists_12_commits(dashboard: Dashboard) -> None:
    """TC-101: 12 commits with hash, author, date and Debt Score."""
    tdea = dashboard.tdea
    dashboard.open(page("fx-s", "/commits", tdea), "main.content table tbody tr")
    shown = [[cell.text for cell in row.find_elements("css selector", "td")]
             for row in dashboard.all("main.content table tbody tr")]
    stored = list(reversed(aggregates(tdea, "fx-s")))  # newest first, as shown
    assert len(shown) == 12
    for cells, row in zip(shown, stored):
        sequence, sha, date, author, _message, _sloc, score = cells[:7]
        assert (sequence, sha, date, author) == (
            str(row["sequence"]), row["sha"][:10], utc(row["authored_at"]), row["author_name"])
        assert score == f"{row['debt_score']:.2f}"
