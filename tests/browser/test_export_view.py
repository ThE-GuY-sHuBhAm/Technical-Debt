"""Workbook v1.2 browser cases of the Export section and dashboard load time: TC-102,
TC-106, TC-111, TC-112 and TC-124 (FR-40 to FR-43, NFR-02, NFR-03).

Each download is started by clicking the Export section's link in Chrome and timed until
the complete file is in the download folder, which is later than the moment a download
starts, so the TC-111 and TC-112 limits are checked strictly. The test machine is not
the reference machine of NFR-01 to NFR-03 (8 GB RAM, 2.0 GHz quad core), so the times
are developer evidence for those cases.
"""

from __future__ import annotations

import csv

from browser_support import Dashboard

CSV_HEADER = ["commit_hash", "commit_sequence", "timestamp", "author", "SLOC", "mean_CC",
              "mean_MI", "total_smells", "smell_density", "debt_score", "debt_delta",
              "debt_classification"]
READY = r"""
const chart = document.querySelector('figure[aria-label="Debt Score timeline"] canvas');
const card = document.querySelector('section[aria-label="Repository overview"]');
const patterns = [...document.querySelectorAll('main.content h2')]
  .some((item) => item.innerText === 'Debt patterns');
return Boolean(chart && chart.tdeaChart && chart.tdeaChart.chartArea && card && patterns
  && !document.querySelector('main.content [aria-live="polite"]'));
"""


def export_page(dashboard: Dashboard, name: str) -> None:
    """Open the Export section of fixture ``name``."""
    repository_id = dashboard.tdea.ids[name]
    dashboard.open(f"/export?repo={repository_id}", "main.content .export-grid")


def test_tc102_pdf_downloads_from_the_export_section(dashboard: Dashboard) -> None:
    """TC-102 (partly): the PDF of FX-M downloads and is a complete PDF file.

    Opening it in a standard PDF viewer is the manual part of TC-102 and is not done here.
    """
    export_page(dashboard, "fx-m")
    path, _seconds = dashboard.download("Download PDF", ".pdf", 10)
    data = path.read_bytes()
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    assert path.name.endswith(".pdf")


def test_tc106_csv_header_row(dashboard: Dashboard) -> None:
    """TC-106: row 1 of the downloaded CSV is the FR-41 header."""
    export_page(dashboard, "fx-m")
    path, _seconds = dashboard.download("Download CSV", ".csv", 5)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == CSV_HEADER and len(rows) == 51


def test_tc111_pdf_of_fx_l_within_10_seconds(dashboard: Dashboard) -> None:
    """TC-111: the PDF of FX-L (200 commits) is downloaded within 10 seconds."""
    export_page(dashboard, "fx-l")
    path, seconds = dashboard.download("Download PDF", ".pdf", 10)
    assert path.read_bytes().startswith(b"%PDF-")
    assert seconds <= 10, f"{seconds:.1f} s"


def test_tc112_csv_of_fx_l_within_5_seconds(dashboard: Dashboard) -> None:
    """TC-112: the CSV of FX-L (200 commits) is downloaded within 5 seconds."""
    export_page(dashboard, "fx-l")
    path, seconds = dashboard.download("Download CSV", ".csv", 5)
    assert path.read_text(encoding="utf-8").startswith(",".join(CSV_HEADER))
    assert seconds <= 5, f"{seconds:.1f} s"


def test_tc124_fx_l_dashboard_loads_within_3_seconds(dashboard: Dashboard) -> None:
    """TC-124: three loads of FX-L's dashboard, each ready within 3 seconds.

    Measured in the browser from navigation start until the overview card, the Debt
    Score chart and the pattern table are all shown (Navigation Timing).
    """
    repository_id = dashboard.tdea.ids["fx-l"]
    times = []
    for _attempt in range(3):
        dashboard.driver.get(f"{dashboard.tdea.base_url}/overview?repo={repository_id}")
        dashboard.until(lambda: dashboard.driver.execute_script(READY), 10,
                        "the dashboard did not finish loading")
        times.append(dashboard.driver.execute_script("return performance.now();") / 1000)
    assert all(seconds <= 3 for seconds in times), times
