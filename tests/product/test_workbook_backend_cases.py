"""Workbook v1.2 cases checked through the REST API and tdea.db, without a browser.

Covered: TC-017, TC-018, TC-020 to TC-024 and TC-032 (clone, history and clean-up),
TC-030, TC-031 (checkout), TC-055 to TC-066 (persistence, queries, cache, audit),
TC-103 to TC-105 and TC-107 to TC-110 (exports), TC-114 and TC-120 (comparison),
TC-125 (API response time), TC-127 to TC-130 and TC-133 (reliability and error
quality), TC-015 and TC-141 (token not stored). Every analysis goes through the real
AnalysisService and Flask application. Only the FR-03 check is stubbed (product_support).

Where the generated fixtures differ from the workbook's text, the test says so:

* FX-S deletes c.py in commit #10, not #7 (TC-031).
* FX-S has no src/util.py; TC-061 uses fxs/geometry.py.
* FX-ERR has four failing files, not three (TC-066).
* TDEA's complexity metric types are mean_cc and max_cc; the workbook calls the type
  'cc' (TC-062).

The PDF's text is read with pypdf. Its standard deviation is the population value. The
workbook suggests pandas describe(), which gives the sample value. TC-104 checks the
population value; the comparison with the sample value is a strict expected failure
until the open decision is settled.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path

import make_fx_product
import pytest
from fixture_git import FixtureRepo, Identity, git_output
from flask.testing import FlaskClient
from product_support import (FX_ERR_URL, FX_S_URL, CountingSources, StubClient, analysed,
                             local_service)
from pypdf import PdfReader

from tdea.aggregator.analysis_service import AUDIT_FILE, AnalysisService
from tdea.aggregator.product_models import COMPLETE, ERROR
from tdea.aggregator.product_store import ProductStore
from tdea.aggregator.scoring import WEIGHTS
from tdea.analyzer.product_analyzer import ProductAnalyzer
from tdea.analyzer.product_engine import ProductEngine
from tdea.api.app import create_app
from tdea.git_handler.errors import ErrorCode, TdeaError

FX_M_URL = "https://github.com/tdea-fixtures/fx-m"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class Workspace:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """FX-S, FX-M and FX-ERR analysed once through the API, with clones counted."""

    def __init__(self, root: Path) -> None:
        """Build the fixtures and analyse them."""
        self.root = root
        self.fx_s = make_fx_product.build_fx_s(root / "fx-s")
        self.fx_m = make_fx_product.build_fx_m(root / "fx-m")
        self.fx_err = make_fx_product.build_fx_err(root / "fx-err")
        self.sources = CountingSources({"tdea-fixtures/fx-s": self.fx_s.path,
                                        "tdea-fixtures/fx-m": self.fx_m.path,
                                        "tdea-fixtures/fx-err": self.fx_err.path})
        self.service = local_service(root / "ws", self.sources)
        self.client = create_app(self.service).test_client()
        self.ids = {}
        for name, url in (("fx-s", FX_S_URL), ("fx-m", FX_M_URL), ("fx-err", FX_ERR_URL)):
            answer = self.client.post("/api/analyses", json={"url": url})
            assert answer.status_code == 202, answer.json
            self.ids[name] = answer.json["repository_id"]
            assert self.service.wait(self.ids[name], 600)

    @property
    def db(self) -> Path:
        """Return the path of tdea.db."""
        return self.service.store.path

    def rows(self, query: str, *parameters: object) -> list[tuple]:
        """Run one query on tdea.db."""
        with sqlite3.connect(self.db) as connection:
            return connection.execute(query, parameters).fetchall()

    def audit(self) -> list[dict]:
        """Return the FR-23 audit entries."""
        path = self.db.parent / AUDIT_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(name="ws", scope="module")
def _ws(tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    """Return the module's analysed workspace."""
    return Workspace(tmp_path_factory.mktemp("backend"))


def error_parts(response, status: int) -> dict:
    """Check the FR-05 / NFR-07 error shape and return its three parts."""
    assert response.status_code == status, response.get_json()
    error = response.get_json()["error"]
    assert set(error) == {"type", "cause", "action"} and all(error.values())
    for raw in ("Traceback", "Exception", 'File "'):
        assert raw not in json.dumps(error)
    return error


# CLONE AND COMMIT HISTORY ------------------------------------------------------------

class CloneInspector(ProductAnalyzer):  # pylint: disable=too-few-public-methods
    """The product analyzer, which first records the fresh clone (TC-017, TC-018)."""

    def __init__(self) -> None:
        """Record nothing yet."""
        super().__init__()
        self.commits: list[str] = []
        self.remote_branches: list[str] = []

    def analyse(self, repository, commits, *rest):
        """Record the clone's history and remote branches, then analyse as usual.

        This is the pause after the clone step that the cases ask for: TDEA has no debug
        mode, so the test looks at the clone at the point where analysis would begin.
        """
        self.commits = git_output(repository.path, "log", "--oneline").splitlines()
        self.remote_branches = git_output(repository.path, "branch", "-r",
                                          "--format=%(refname:short)").split()
        return super().analyse(repository, commits, *rest)


def test_tc017_tc018_clone_has_every_commit_and_branch(tmp_path: Path) -> None:
    """TC-017: the clone has FX-S's 12 commits. TC-018: and all 3 of its branches."""
    fx_s = make_fx_product.build_fx_s(tmp_path / "fx-s")
    inspector = CloneInspector()
    engine = ProductEngine(client=StubClient(), analyzer=inspector,
                           clone_source=lambda repo: fx_s.path, temp_root=tmp_path / "ws")
    service = AnalysisService(ProductStore(tmp_path / "ws" / "tdea.db"), engine,
                              lock_directory=tmp_path / "ws" / "lock")
    analysed(service, FX_S_URL)
    assert len(inspector.commits) == 12
    heads = sorted(line.split("refs/heads/")[1] for line in
                   git_output(fx_s.path, "ls-remote", "--heads", str(fx_s.path)).splitlines())
    assert heads == ["develop", "feature/charts", "main"]
    assert sorted(item.removeprefix("origin/") for item in inspector.remote_branches
                  if item not in ("origin", "origin/HEAD")) == heads


def test_tc020_tc022_stored_hashes_and_authors_equal_git_log(ws: Workspace) -> None:
    """TC-020: 12 rows whose hashes equal git log. TC-022: author name and email too."""
    stored = ws.rows("SELECT sha, author_name, author_email FROM commits "
                     "WHERE repository_id = ? ORDER BY sequence", ws.ids["fx-s"])
    logged = [tuple(line.split("|")) for line in reversed(git_output(
        ws.fx_s.path, "log", "--format=%H|%an|%ae").splitlines())]
    assert len(stored) == 12 and stored == logged


def test_tc021_message_of_120_characters_is_stored_as_72(ws: Workspace) -> None:
    """TC-021: the 120-character message is stored as its first 72 characters."""
    long = make_fx_product.LONG_MESSAGE
    (message,), = ws.rows("SELECT message FROM commits WHERE repository_id = ? AND "
                          "message LIKE ?", ws.ids["fx-s"], long[:20] + "%")
    assert len(long) == 120 and len(message) == 72 and message == long[:72]


def test_tc023_commit_time_is_stored_in_utc(ws: Workspace) -> None:
    """TC-023: the 2026-09-10 18:30 +05:30 commit is stored as 2026-09-10T13:00:00Z."""
    stored = [value for value, in ws.rows(
        "SELECT authored_at FROM commits WHERE repository_id = ?", ws.ids["fx-s"])]
    assert "2026-09-10T13:00:00Z" in stored


def test_tc024_changed_files_of_the_add_modify_delete_commit(ws: Workspace) -> None:
    """TC-024: commit #10 lists a.py added, b.py modified and c.py deleted."""
    (changed,), = ws.rows("SELECT changed_files FROM commits WHERE repository_id = ? "
                          "AND sequence = 10", ws.ids["fx-s"])
    assert sorted(map(tuple, json.loads(changed))) == [
        ("fxs/a.py", "added"), ("fxs/b.py", "modified"), ("fxs/c.py", "deleted")]


def test_tc032_temporary_clone_is_gone_after_the_analysis(ws: Workspace) -> None:
    """TC-032: after the analyses complete, the temporary folder holds no clone."""
    assert ws.sources.clones  # analyses did clone into the workspace
    left = [path.name for path in (ws.root / "ws").iterdir()
            if path.name not in ("tdea.db", AUDIT_FILE, "lock")
            and not path.name.startswith("tdea.db-")]
    assert left == []


# COMMIT CHECKOUT ---------------------------------------------------------------------

@pytest.mark.parametrize("sequence", [2, 6, 12])
def test_tc030_loc_equals_the_file_at_that_commit(ws: Workspace, sequence: int) -> None:
    """TC-030: at commits #2, #6 and #12 each file's LOC equals `git show sha:path`."""
    sha = ws.fx_s.shas[sequence - 1]
    stored = ws.rows("SELECT f.path, f.loc FROM file_metrics f JOIN commits c "
                     "USING (commit_id) WHERE c.repository_id = ? AND c.sha = ?",
                     ws.ids["fx-s"], sha)
    assert stored
    for path, loc in stored:
        content = git_output(ws.fx_s.path, "show", f"{sha}:{path}")
        assert loc == len(content.splitlines()), (sequence, path)


def test_tc031_deleted_file_has_rows_only_while_it_exists(ws: Workspace) -> None:
    """TC-031: fxs/c.py, deleted in FX-S commit #10, has rows for #1 to #9 only."""
    sequences = [sequence for sequence, in ws.rows(
        "SELECT c.sequence FROM file_metrics f JOIN commits c USING (commit_id) "
        "WHERE c.repository_id = ? AND f.path = 'fxs/c.py' ORDER BY c.sequence",
        ws.ids["fx-s"])]
    assert sequences == list(range(1, 10))


# DATABASE SCHEMA ---------------------------------------------------------------------

def test_tc055_four_tables_with_the_fr19_keys(ws: Workspace) -> None:
    """TC-055: repositories, commits, file_metrics, aggregate_metrics with FR-19's keys."""
    expected = {"repositories": ("repository_id", None),
                "commits": ("commit_id", ("repository_id", "repositories")),
                "file_metrics": ("metric_id", ("commit_id", "commits")),
                "aggregate_metrics": ("agg_id", ("commit_id", "commits"))}
    with sqlite3.connect(ws.db) as connection:
        tables = {name for name, in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert set(expected) <= tables
        for table, (key, foreign) in expected.items():
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [column[1] for column in columns if column[5]] == [key], table
            links = {(row[3], row[2]) for row in connection.execute(
                f"PRAGMA foreign_key_list({table})")}
            assert links == ({foreign} if foreign else set()), table


def test_tc056_no_orphaned_records(ws: Workspace) -> None:
    """TC-056: PRAGMA foreign_key_check returns no rows."""
    assert ws.rows("PRAGMA foreign_key_check") == []


def test_tc057_second_aggregate_row_for_a_commit_is_refused(ws: Workspace,
                                                           tmp_path: Path) -> None:
    """TC-057: a second aggregate_metrics row for an existing commit_id fails (UNIQUE).

    The insert runs on a copy of tdea.db, so the shared store is left as it was.
    """
    copy = tmp_path / "copy.db"
    with sqlite3.connect(ws.db) as source, sqlite3.connect(copy) as target:
        source.backup(target)
    with sqlite3.connect(copy) as connection:
        row = connection.execute("SELECT * FROM aggregate_metrics LIMIT 1").fetchone()
        columns = [item[1] for item in connection.execute(
            "PRAGMA table_info(aggregate_metrics)")]
        values = dict(zip(columns, row))
        del values["agg_id"]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                f"INSERT INTO aggregate_metrics ({', '.join(values)}) "
                f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()))


# SESSION PERSISTENCE AND CACHE ---------------------------------------------------------

def restarted(ws: Workspace) -> tuple[AnalysisService, CountingSources, FlaskClient]:
    """Return a new service and client over the same tdea.db (a server restart)."""
    sources = CountingSources(dict(ws.sources))
    service = local_service(ws.root / "ws", sources)
    return service, sources, create_app(service).test_client()


def test_tc058_tc109_results_and_exports_after_a_restart(ws: Workspace) -> None:
    """TC-058, TC-109: after a restart the same results, CSV and PDF; nothing re-run."""
    repository_id = ws.ids["fx-m"]
    before = {path: ws.client.get(f"/api/repositories/{repository_id}{path}").data
              for path in ("/commits", "/export.csv")}
    audit = len(ws.audit())
    _service, sources, client = restarted(ws)
    for path, content in before.items():
        assert client.get(f"/api/repositories/{repository_id}{path}").data == content
    pdf = client.get(f"/api/repositories/{repository_id}/report.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert sources.clones == [] and len(ws.audit()) == audit


def test_tc064_resubmission_is_served_from_the_cache_within_3_seconds(
        ws: Workspace) -> None:
    """TC-064: the same URL again is answered from tdea.db, in under 3 s, with no clone."""
    clones, audit = len(ws.sources.clones), len(ws.audit())
    started = time.monotonic()
    answer = ws.client.post("/api/analyses", json={"url": FX_S_URL})
    elapsed = time.monotonic() - started
    assert answer.status_code == 200 and answer.json["cached"] is True
    assert elapsed < 3, f"{elapsed:.2f} s"
    assert len(ws.sources.clones) == clones and len(ws.audit()) == audit


def test_tc065_a_new_url_runs_the_full_pipeline(ws: Workspace) -> None:
    """TC-065: FX-M after FX-S was cloned and analysed; it has its own results."""
    assert "tdea-fixtures/fx-m" in ws.sources.clones
    entries = [entry for entry in ws.audit() if entry["url"] == FX_M_URL]
    assert entries and entries[0]["outcome"] == COMPLETE
    assert entries[0]["commits_processed"] == 50
    shas = {sha for sha, in ws.rows("SELECT sha FROM commits WHERE repository_id = ?",
                                    ws.ids["fx-m"])}
    assert shas == set(ws.fx_m.shas)


def test_tc066_audit_entry_of_fx_err(ws: Workspace) -> None:
    """TC-066: URL, start and end times, commits requested and processed, error count.

    The generated FX-ERR has four failing files (syntax error, Latin-1, empty and
    binary), where the workbook's text says three; the count is checked against them.
    """
    entry = [item for item in ws.audit() if item["url"] == FX_ERR_URL][-1]
    assert {"url", "started_at", "completed_at", "commits_requested", "commits_processed",
            "file_errors", "outcome"} <= set(entry)
    assert entry["started_at"] <= entry["completed_at"]
    assert (entry["commits_requested"], entry["commits_processed"], entry["file_errors"]) \
        == (1, 1, 4)


# QUERIES (FR-21) --------------------------------------------------------------------

def metric(ws: Workspace, repository_id: int, **query: str):
    """Return the answer of the FR-21 query endpoint."""
    return ws.client.get(f"/api/repositories/{repository_id}/metrics", query_string=query)


def test_tc059_metrics_belong_to_the_repository_asked_for(ws: Workspace) -> None:
    """TC-059: with FX-S and FX-M stored, a query for FX-S returns FX-S commits only."""
    answer = metric(ws, ws.ids["fx-s"], metric="debt_score").json
    assert [row["sha"] for row in answer] == list(ws.fx_s.shas)


def test_tc060_metrics_of_one_commit(ws: Workspace) -> None:
    """TC-060: file metrics for commit #6's hash are that commit's rows only."""
    sha = ws.fx_s.shas[5]
    answer = metric(ws, ws.ids["fx-s"], metric="loc", sha=sha).json
    stored = ws.rows("SELECT f.path, f.loc FROM file_metrics f JOIN commits c "
                     "USING (commit_id) WHERE c.sha = ? AND c.repository_id = ? "
                     "ORDER BY f.path", sha, ws.ids["fx-s"])
    assert {row["sha"] for row in answer} == {sha}
    assert [(row["path"], row["value"]) for row in answer] == stored


def test_tc061_metrics_of_one_file_in_commit_order(ws: Workspace) -> None:
    """TC-061: one row per analysed commit containing fxs/geometry.py, in commit order."""
    answer = metric(ws, ws.ids["fx-s"], metric="sloc", path="fxs/geometry.py").json
    assert [row["sequence"] for row in answer] == list(range(1, 13))
    assert {row["path"] for row in answer} == {"fxs/geometry.py"}


def test_tc062_one_metric_type_only(ws: Workspace) -> None:
    """TC-062: asking for the CC type returns CC values and no other metric field.

    TDEA names the type mean_cc (and max_cc); 'cc' itself is refused with the list of
    names in a three-part 400.
    """
    answer = metric(ws, ws.ids["fx-s"], metric="mean_cc").json
    assert all(set(row) == {"sequence", "sha", "path", "value"} for row in answer)
    stored = [value for value, in ws.rows(
        "SELECT a.mean_cc FROM aggregate_metrics a JOIN commits c USING (commit_id) "
        "WHERE c.repository_id = ? ORDER BY c.sequence", ws.ids["fx-s"])]
    assert [row["value"] for row in answer] == stored
    assert "mean_cc" in error_parts(metric(ws, ws.ids["fx-s"], metric="cc"), 400)["action"]


def test_tc063_unknown_commit_is_a_structured_not_found(ws: Workspace) -> None:
    """TC-063: an unknown hash gives a structured 'not found', not a server error."""
    response = ws.client.get(f"/api/repositories/{ws.ids['fx-s']}/commits/{'0' * 40}")
    assert error_parts(response, 404)["type"] == "Not found"


# EXPORTS -----------------------------------------------------------------------------

@pytest.fixture(name="fx_m_pdf", scope="module")
def _fx_m_pdf(ws: Workspace) -> str:
    """Return the text of FX-M's PDF report."""
    response = ws.client.get(f"/api/repositories/{ws.ids['fx-m']}/report.pdf")
    assert response.status_code == 200
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.data)).pages)


def fx_m_csv(ws: Workspace) -> list[dict[str, str]]:
    """Return FX-M's CSV export as rows."""
    response = ws.client.get(f"/api/repositories/{ws.ids['fx-m']}/export.csv")
    return list(csv.DictReader(io.StringIO(response.data.decode("utf-8"))))


def test_tc103_pdf_has_metadata_timeline_and_top_10(ws: Workspace, fx_m_pdf: str) -> None:
    """TC-103: sections (a) metadata, (b) the Debt Score timeline, (c) the top-10 table."""
    for text in ("TDEA report: tdea-fixtures/fx-m", "Repository", FX_M_URL,
                 "Commits analysed", "Debt Score timeline", "Top 10 high-risk files"):
        assert text in fx_m_pdf, text
    for path in ("fxm/alpha.py", "fxm/epsilon.py"):
        assert path in fx_m_pdf
    response = ws.client.get(f"/api/repositories/{ws.ids['fx-m']}/report.pdf")
    reader = PdfReader(io.BytesIO(response.data))
    drawn = b"".join(page.get_contents().get_data() for page in reader.pages)
    assert drawn.count(b" l") > 50  # the timeline is drawn as vector lines


STAT_COLUMNS = {"SLOC": "SLOC", "Mean cyclomatic complexity": "mean_CC",
                "Mean Maintainability Index": "mean_MI", "Total code smells": "total_smells",
                "Mean smell density": "smell_density", "Debt Score": "debt_score"}


def pdf_statistics(text: str) -> dict[str, tuple[float, float, float, float]]:
    """Return mean, minimum, maximum and std of each metric in the PDF's statistics table.

    pypdf gives each table cell on its own line: a metric's label, then its four values.
    """
    lines = [item.strip() for item in text.splitlines()]
    found = {}
    for index, item in enumerate(lines[:-4]):
        if item in STAT_COLUMNS:
            try:
                found[item] = tuple(float(value) for value in lines[index + 1:index + 5])
            except ValueError:
                continue
    assert set(found) == set(STAT_COLUMNS), found
    return found


def test_tc104_pdf_statistics_match_the_csv(ws: Workspace, fx_m_pdf: str) -> None:
    """TC-104: mean, minimum and maximum of every metric equal the CSV's, to 2 decimals.

    The PDF gives the population standard deviation; it is compared here as such. The
    comparison with pandas describe() (sample standard deviation) is the next test.
    """
    rows = fx_m_csv(ws)
    table = pdf_statistics(fx_m_pdf)
    for label, column in STAT_COLUMNS.items():
        mean, low, high, deviation = table[label]
        values = [float(row[column]) for row in rows]
        assert round(mean, 2) == round(statistics.fmean(values), 2), label
        assert round(low, 2) == round(min(values), 2) and round(high, 2) == round(max(values), 2)
        assert round(deviation, 2) == round(statistics.pstdev(values), 2), label


@pytest.mark.xfail(strict=True, reason="open decision TC-104: the PDF reports the "
                   "population standard deviation; pandas describe() gives the sample one")
def test_tc104_standard_deviation_as_pandas_describe(ws: Workspace, fx_m_pdf: str) -> None:
    """TC-104 as written: the PDF's std equals pandas describe()'s (sample, n - 1)."""
    rows = fx_m_csv(ws)
    table = pdf_statistics(fx_m_pdf)
    for label, column in STAT_COLUMNS.items():
        deviation = table[label][3]
        values = [float(row[column]) for row in rows]
        assert round(deviation, 2) == round(statistics.stdev(values), 2), label


def test_tc105_pdf_patterns_and_methodology(ws: Workspace, fx_m_pdf: str) -> None:
    """TC-105: patterns with commit ranges; radon, Pylint and the four weights named."""
    patterns = ws.client.get(f"/api/repositories/{ws.ids['fx-m']}/patterns").json
    assert "Debt patterns" in fx_m_pdf
    for row in patterns:
        assert f"{row['first_sequence']}-{row['last_sequence']}" in fx_m_pdf
    for text in ("radon", "Pylint", "Methodology"):
        assert text in fx_m_pdf
    flat = " ".join(fx_m_pdf.split())
    for weight in sorted(set(WEIGHTS.values())):
        assert f"{weight:.2f}" in flat
    assert [f"{value:.2f}" for value in (WEIGHTS["cc"], WEIGHTS["smells"], WEIGHTS["mi"],
                                         WEIGHTS["sloc_growth"])] == \
        ["0.30", "0.30", "0.25", "0.15"]


def test_tc107_fx_m_csv_has_50_rows_equal_to_the_database(ws: Workspace) -> None:
    """TC-107: 50 data rows; rows 1, 25 and 50 equal tdea.db."""
    rows = fx_m_csv(ws)
    assert len(rows) == 50
    for sequence in (1, 25, 50):
        row = rows[sequence - 1]
        stored = ws.rows("SELECT c.sha, a.total_sloc, a.mean_cc, a.mean_mi, a.total_smells, "
                         "a.debt_score, a.debt_delta, a.classification FROM commits c JOIN "
                         "aggregate_metrics a USING (commit_id) WHERE c.repository_id = ? "
                         "AND c.sequence = ?", ws.ids["fx-m"], sequence)[0]
        assert (row["commit_hash"], int(row["SLOC"]), float(row["mean_CC"]),
                float(row["mean_MI"]), int(row["total_smells"]), float(row["debt_score"]),
                float(row["debt_delta"]), row["debt_classification"]) == stored


def test_tc108_author_with_a_comma_stays_in_one_cell(tmp_path: Path) -> None:
    """TC-108: author "Doe, Jane" parses as one CSV cell (csv module; pandas not used)."""
    repo = FixtureRepo(tmp_path / "comma")
    repo.commit("Add a module", {"pkg/m.py": '"""M."""\n\nVALUE = 1\n'},
                author=Identity("Doe, Jane", "jane.doe@example.invalid"),
                date="2026-09-01T10:00:00+00:00")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/comma": repo.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/comma")
    client = create_app(service).test_client()
    text = client.get(f"/api/repositories/{submission.repository_id}/export.csv").data
    rows = list(csv.DictReader(io.StringIO(text.decode("utf-8"))))
    assert [row["author"] for row in rows] == ["Doe, Jane"]
    assert len(rows[0]) == 12


def test_tc110_export_of_an_unknown_repository(ws: Workspace) -> None:
    """TC-110: repository 999 gives a structured not-found message, not a server error.

    The workbook's wording is 'No analysis found for this repository'; TDEA answers
    "Not found: No repository 999." with a next step.
    """
    for path in ("/export.csv", "/report.pdf"):
        error = error_parts(ws.client.get(f"/api/repositories/999{path}"), 404)
        assert error["type"] == "Not found" and "999" in error["cause"]


# COMPARISON ------------------------------------------------------------------------

@pytest.fixture(name="pair", scope="module")
def _pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[FlaskClient, AnalysisService,
                                                             int, int]:
    """Analyse the comparison pair (10 and 15 commits) through POST /api/comparisons."""
    root = tmp_path_factory.mktemp("pair")
    sources = {f"tdea-fixtures/{key}": make_fx_product.BUILDERS[key](root / key).path
               for key in ("fx-cmp-a", "fx-cmp-b")}
    service = local_service(root / "ws", sources)
    client = create_app(service).test_client()
    answer = client.post("/api/comparisons", json={
        "url_1": "https://github.com/tdea-fixtures/fx-cmp-a",
        "url_2": "https://github.com/tdea-fixtures/fx-cmp-b"})
    assert answer.status_code == 202
    first, second = answer.json["repo_1"], answer.json["repo_2"]
    assert service.wait(first, 300) and service.wait(second, 300)
    return client, service, first, second


def test_tc114_each_repository_has_its_own_rows(pair) -> None:
    """TC-114: two repository rows, each with its own commits and metrics."""
    _client, service, first, second = pair
    with sqlite3.connect(service.store.path) as connection:
        assert connection.execute("SELECT count(*) FROM repositories").fetchone() == (2,)
        counts = dict(connection.execute(
            "SELECT c.repository_id, count(*) FROM commits c JOIN aggregate_metrics a "
            "USING (commit_id) GROUP BY c.repository_id").fetchall())
        shared = connection.execute(
            "SELECT count(*) FROM commits a JOIN commits b ON a.sha = b.sha "
            "AND a.repository_id < b.repository_id").fetchone()
    assert counts == {first: 10, second: 15} and shared == (0,)


def test_tc120_unequal_lengths_give_15_rows_with_blanks(pair) -> None:
    """TC-120: 15 data rows; repo_1 cells blank for sequences 11 to 15 (OI-06 proposal)."""
    client, _service, first, second = pair
    text = client.get(f"/api/comparison.csv?repo_1={first}&repo_2={second}").data
    rows = list(csv.reader(io.StringIO(text.decode("utf-8"))))[1:]
    assert [row[0] for row in rows] == [str(number) for number in range(1, 16)]
    assert all(all(cell for cell in row) for row in rows[:10])
    assert all(row[1:4] == ["", "", ""] and all(row[4:]) for row in rows[10:])


# PERFORMANCE AND RELIABILITY ---------------------------------------------------------

def test_tc125_every_endpoint_p95_within_2_seconds(ws: Workspace) -> None:
    """TC-125: each endpoint called 20 times in sequence; 95th percentile <= 2 s."""
    repository_id, sha = ws.ids["fx-m"], ws.fx_m.shas[10]
    paths = ["/api/repositories", f"/api/repositories/{repository_id}",
             f"/api/repositories/{repository_id}/commits",
             f"/api/repositories/{repository_id}/commits/{sha}",
             f"/api/repositories/{repository_id}/high-risk",
             f"/api/repositories/{repository_id}/patterns",
             f"/api/repositories/{repository_id}/metrics?metric=debt_score",
             f"/api/repositories/{repository_id}/export.csv",
             f"/api/repositories/{repository_id}/report.pdf",
             f"/api/comparison?repo_1={ws.ids['fx-s']}&repo_2={repository_id}",
             f"/api/comparison.csv?repo_1={ws.ids['fx-s']}&repo_2={repository_id}"]
    slow = {}
    for path in paths:
        times = []
        for _call in range(20):
            started = time.monotonic()
            assert ws.client.get(path).status_code == 200, path
            times.append(time.monotonic() - started)
        p95 = sorted(times)[18]  # the 19th of 20 values
        if p95 > 2:
            slow[path] = p95
    assert not slow, slow


def test_tc127_five_corrupt_files_among_45_valid(tmp_path: Path, caplog) -> None:
    """TC-127: status Complete, 5 logged file errors, all 45 valid files measured."""
    files: dict[str, str | bytes | None] = {
        f"pkg/valid_{index:02d}.py": f'"""Valid {index}."""\n\nVALUE = {index}\n'
        for index in range(45)}
    files.update({f"pkg/corrupt_{index}.py": f"def broken_{index}(:\n    return\n"
                  for index in range(5)})
    repo = FixtureRepo(tmp_path / "corrupt")
    repo.commit("Fifty files", files, author=make_fx_product.MAINTAINER,
                date="2026-09-01T10:00:00+00:00")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/corrupt": repo.path})
    with caplog.at_level("WARNING"):
        submission = analysed(service, "https://github.com/tdea-fixtures/corrupt")
    stored = service.store.repository(submission.repository_id)
    assert (stored.status, stored.file_errors) == (COMPLETE, 5)
    logged = [record for record in caplog.records
              if record.getMessage().startswith("File-level analysis failure")]
    assert sorted(record.args[0] for record in logged) == \
        [f"pkg/corrupt_{index}.py" for index in range(5)]
    with sqlite3.connect(service.store.path) as connection:
        paths = [path for path, in connection.execute("SELECT path FROM file_metrics")]
    assert sorted(paths) == [f"pkg/valid_{index:02d}.py" for index in range(45)]


def test_tc128_three_fx_m_runs_give_the_same_csv_md5(ws: Workspace, tmp_path: Path) -> None:
    """TC-128: FX-M analysed three times from an empty cache; the CSVs' MD5 is equal.

    With TDEA_TC145_OUT set, the MD5 is also written to that file, so that CI can compare
    the value of every operating system and Python version (TC-145).
    """
    digests = [hashlib.md5(ws.client.get(f"/api/repositories/{ws.ids['fx-m']}/export.csv")
                           .data).hexdigest()]
    for number in range(2):
        service = local_service(tmp_path / str(number), {"tdea-fixtures/fx-m": ws.fx_m.path})
        submission = analysed(service, FX_M_URL)
        client = create_app(service).test_client()
        digests.append(hashlib.md5(client.get(
            f"/api/repositories/{submission.repository_id}/export.csv").data).hexdigest())
    assert len(set(digests)) == 1, digests
    if os.environ.get("TDEA_TC145_OUT"):
        Path(os.environ["TDEA_TC145_OUT"]).write_text(digests[0] + "\n", encoding="utf-8")


SUBMISSION_FAILURES = [
    ("non-GitHub host", {"url": "https://gitlab.com/gitlab-org/gitlab"}, None, 400),
    ("missing protocol", {"url": "github.com/psf/requests"}, None, 400),
    ("empty URL", {"url": ""}, None, 400),
    ("owner-only URL", {"url": "https://github.com/psf"}, None, 400),
    ("non-existent repository", {"url": FX_S_URL},
     TdeaError(ErrorCode.NOT_FOUND, "Repository not found", "No such repository.",
               "Check the name."), 422),
    ("private repository", {"url": FX_S_URL},
     TdeaError(ErrorCode.PRIVATE_REPOSITORY, "Repository not accessible", "It is private.",
               "Use a public repository."), 422),
    ("empty repository", {"url": FX_S_URL},
     TdeaError(ErrorCode.EMPTY_REPOSITORY, "Empty repository", "No commits.",
               "Choose another repository."), 422),
    ("invalid PAT", {"url": FX_S_URL, "token": "ghp_invalidtoken000000000000000"},
     TdeaError(ErrorCode.AUTH_FAILED, "Authentication failed", "Token refused.",
               "Check the token."), 422),
    ("N = 0", {"url": FX_S_URL, "sampling_n": 0}, None, 400),
]


@pytest.mark.parametrize("case, body, failure, status", SUBMISSION_FAILURES,
                         ids=[case for case, *_rest in SUBMISSION_FAILURES])
def test_tc129_tc133_invalid_input_gets_400_or_422_in_three_parts(
        tmp_path: Path, case: str, body: dict, failure: TdeaError | None,
        status: int) -> None:
    """TC-129 and TC-133: each invalid input is refused with a structured 400 or 422.

    The FR-03 answers (404, private, empty, 401) come from the stub. The repository
    without .py files is the tenth input; see the next test.
    """
    service = local_service(tmp_path, {}, StubClient(failure=failure))
    response = create_app(service).test_client().post("/api/analyses", json=body)
    error = error_parts(response, status)
    assert "ghp_invalidtoken" not in json.dumps(error), case


def test_tc129_repository_without_python_is_refused_only_after_the_clone(
        tmp_path: Path) -> None:
    """TC-129, tenth input (partly): a repository without .py files.

    The submission is accepted (202), because FR-03 cannot see the languages; the
    analysis then ends as an Error with a three-part message. The workbook expects 400 or
    422 at submission, so this input does not meet the case as written.
    """
    repo = FixtureRepo(tmp_path / "docs-only")
    repo.commit("Docs only", {"README.md": "# Docs\n"}, author=make_fx_product.MAINTAINER,
                date="2026-09-01T10:00:00+00:00")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/docs-only": repo.path})
    client = create_app(service).test_client()
    answer = client.post("/api/analyses",
                         json={"url": "https://github.com/tdea-fixtures/docs-only"})
    assert answer.status_code == 202
    assert service.wait(answer.json["repository_id"], 120)
    overview = client.get(f"/api/repositories/{answer.json['repository_id']}").json
    assert overview["status"] == ERROR and overview["error"]["type"]
    assert overview["error"]["cause"] and overview["error"]["action"]


def test_tc130_killed_during_analysis_leaves_a_consistent_database(
        tmp_path: Path) -> None:
    """TC-130: kill -9 at about 20 %: integrity_check ok and no partial commit records."""
    fx_100 = make_fx_product.BUILDERS["fx-100"](tmp_path / "fx-100")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(FIXTURES), os.environ.get("PYTHONPATH", "")]))
    with subprocess.Popen([sys.executable, str(FIXTURES / "product_kill_target.py"),
                           str(tmp_path / "ws"), str(fx_100.path)], env=environment,
                          stdout=subprocess.PIPE, text=True) as process:
        reached = 0
        for line in process.stdout:
            reached = int(line)
            if reached >= 20:
                break
        if os.name == "nt":
            process.kill()
        else:
            os.kill(process.pid, signal.SIGKILL)
        process.wait(30)
    assert reached >= 20
    db = tmp_path / "ws" / "tdea.db"
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM commits").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM commits c LEFT JOIN aggregate_metrics a "
            "USING (commit_id) WHERE a.agg_id IS NULL").fetchone() == (0,)
    service = local_service(tmp_path / "ws", {})
    row = service.store.repositories()[0]
    assert (row.status, row.error_type) == (ERROR, "Analysis interrupted")


# SECURITY ----------------------------------------------------------------------------

def test_tc015_tc141_token_is_not_stored_anywhere(tmp_path: Path, canary_token: str,
                                                  find_canary, caplog) -> None:
    """TC-015, TC-141: after an analysis with a PAT, no stored artefact contains the token.

    Searched: tdea.db, the audit log, the temporary clone folder, both exports, the API
    answers and every log record the analysis wrote.
    """
    fx_s = make_fx_product.build_fx_s(tmp_path / "fx-s")
    client_stub = StubClient()
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-s": fx_s.path}, client_stub)
    client = create_app(service).test_client()
    with caplog.at_level("DEBUG"):
        answer = client.post("/api/analyses", json={"url": FX_S_URL, "token": canary_token})
        assert answer.status_code == 202 and client_stub.tokens == [canary_token]
        assert service.wait(answer.json["repository_id"], 300)
    assert caplog.records and canary_token not in caplog.text
    exports = tmp_path / "exports"
    exports.mkdir()
    repository_id = answer.json["repository_id"]
    for name in ("export.csv", "report.pdf", "commits", ""):
        content = client.get(f"/api/repositories/{repository_id}/{name}".rstrip("/")).data
        (exports / (name or "overview")).write_bytes(content)
    assert canary_token not in answer.get_data(as_text=True)
    assert find_canary(tmp_path / "ws", exports) == []
