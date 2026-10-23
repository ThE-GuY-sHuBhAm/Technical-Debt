"""Product analyses end to end on local fixtures (FR-06 to FR-23, NFR-05 to NFR-08, CON-03).

Each test analyses FX-S or FX-ERR (make_fx_product) through AnalysisService with the
FR-03 check stubbed (product_support), so nothing reaches the network; clones, Pylint
and the background thread are real. Sprint 23 Oct.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import make_fx_product
import pytest
from product_support import FX_ERR_URL, FX_S_URL, StubClient, analysed, local_service
from repo_builder import RepoBuilder

from tdea.aggregator import product_store
from tdea.aggregator.analysis_service import AUDIT_FILE, SubmissionRefused
from tdea.aggregator.lock import AnalysisLock
from tdea.aggregator.product_models import COMPLETE, ERROR, RUNNING
from tdea.aggregator.product_store import ProductStore
from tdea.git_handler.errors import ErrorCode, TdeaError


@pytest.fixture(name="fx_s", scope="module")
def _fx_s(tmp_path_factory: pytest.TempPathFactory) -> make_fx_product.BuiltFixture:
    """Build FX-S once for this module."""
    return make_fx_product.build_fx_s(tmp_path_factory.mktemp("fx") / "fx-s")


def rows(path: Path, query: str) -> list[tuple]:
    """Run one query on a SQLite file."""
    with sqlite3.connect(path) as connection:
        return connection.execute(query).fetchall()


def test_fx_s_is_analysed_stored_and_audited(fx_s, tmp_path: Path, canary_token: str,
                                             find_canary) -> None:
    """FR-06 to FR-19, FR-23: 12 commits, oldest first, with files, aggregates and scores."""
    client = StubClient()
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path}, client)
    submission = analysed(service, FX_S_URL, token=canary_token)
    assert not submission.cached and client.tokens == [canary_token]
    repo = service.store.repository(submission.repository_id)
    assert (repo.status, repo.commits_total, repo.commits_analysed, repo.file_errors) == (
        COMPLETE, 12, 12, 0)
    db = service.store.path
    assert [sha for sha, in rows(db, "SELECT sha FROM commits ORDER BY sequence")] == list(
        fx_s.shas)
    assert rows(db, "SELECT count(*) FROM aggregate_metrics") == [(12,)]
    assert rows(db, "SELECT min(debt_delta), classification FROM aggregate_metrics a JOIN "
                "commits c USING (commit_id) WHERE c.sequence = 1") == [(0.0, "Stable")]
    tc021 = rows(db, "SELECT message FROM commits WHERE sequence = 6")[0][0]
    assert len(tc021) == 72  # FR-07: first 72 characters
    assert rows(db, "SELECT authored_at FROM commits WHERE sequence = 9") == [
        ("2026-09-10T13:00:00Z",)]  # FR-07: UTC
    changes = json.loads(rows(db, "SELECT changed_files FROM commits WHERE sequence = 10")[0][0])
    assert sorted(changes) == [["fxs/a.py", "added"], ["fxs/b.py", "modified"],
                               ["fxs/c.py", "deleted"]]
    files = rows(db, "SELECT path FROM file_metrics f JOIN commits c USING (commit_id) "
                 "WHERE c.sequence = 12 ORDER BY path")
    assert ("fxs/c.py",) not in files and ("fxs/a.py",) in files
    (entry,) = [json.loads(line) for line in
                (tmp_path / AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
    assert entry["url"] == FX_S_URL and entry["outcome"] == COMPLETE
    assert (entry["commits_requested"], entry["commits_processed"], entry["file_errors"]) == (
        12, 12, 0)
    assert entry["started_at"] <= entry["completed_at"]
    assert find_canary(tmp_path) == []  # NFR-19: the token is in no file
    assert not list(tmp_path.glob("tdea-clone-*"))  # FR-10


def test_a_second_submission_is_served_from_the_cache(fx_s, tmp_path: Path) -> None:
    """FR-22: same repository (any case) answers at once; refresh analyses again."""
    client = StubClient()
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path}, client)
    first = analysed(service, FX_S_URL)
    again = service.submit("https://github.com/TDEA-Fixtures/FX-S/")
    assert again.cached and again.repository_id == first.repository_id
    assert len(client.tokens) == 1  # no pipeline stage, not even FR-03, ran again
    fresh = analysed(service, FX_S_URL, refresh=True)
    assert not fresh.cached and fresh.repository_id == first.repository_id
    assert rows(service.store.path, "SELECT count(*) FROM commits") == [(12,)]


def test_results_survive_a_restart(fx_s, tmp_path: Path) -> None:
    """FR-20: a new service over the same tdea.db still has the results."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path})
    submission = analysed(service, FX_S_URL)
    service.store.close()
    reopened = local_service(tmp_path, {})
    assert reopened.submit(FX_S_URL).cached
    assert len(reopened.store.commits(submission.repository_id)) == 12


def test_three_runs_give_identical_csv_values(fx_s, tmp_path: Path) -> None:
    """NFR-06: independent analyses of the same repository store identical values."""
    dumps = []
    for number in range(3):
        service = local_service(tmp_path / str(number), {"tdea-fixtures/fx-s": fx_s.path})
        analysed(service, FX_S_URL)
        dumps.append(rows(service.store.path,
                          "SELECT c.sha, a.total_sloc, a.mean_cc, a.mean_mi, a.total_smells, "
                          "a.mean_smell_density, a.debt_score, a.debt_delta, a.classification "
                          "FROM commits c JOIN aggregate_metrics a USING (commit_id) "
                          "ORDER BY c.sequence"))
    assert dumps[0] == dumps[1] == dumps[2]


def test_failing_files_are_isolated_and_counted(tmp_path: Path) -> None:
    """FR-16, NFR-05: FX-ERR's four failing files are skipped; the valid file is measured."""
    fx_err = make_fx_product.build_fx_err(tmp_path / "fx-err")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-err": fx_err.path})
    submission = analysed(service, FX_ERR_URL)
    repo = service.store.repository(submission.repository_id)
    assert (repo.status, repo.file_errors) == (COMPLETE, 4)
    assert rows(service.store.path, "SELECT path FROM file_metrics") == [("fxerr/valid.py",)]


def test_a_repository_without_python_is_an_error(tmp_path: Path) -> None:
    """NFR-07: a non-Python repository ends with a three-part error, not empty results."""
    builder = RepoBuilder(tmp_path / "docs-only")
    builder.commit("Add docs", {"README.md": "# Docs\n"})
    service = local_service(tmp_path / "ws", {"tdea-fixtures/docs": builder.path})
    submission = analysed(service, "https://github.com/tdea-fixtures/docs")
    repo = service.store.repository(submission.repository_id)
    assert repo.status == ERROR and repo.error_type == "No Python code to analyse"
    assert repo.error_cause and repo.error_action
    assert rows(service.store.path, "SELECT count(*) FROM commits") == [(0,)]


@pytest.mark.parametrize("url, kind", [("http://gitlab.com/a/b", "invalid_url"),
                                       ("", "invalid_url"), (None, "invalid_url")])
def test_invalid_urls_are_refused_before_anything_runs(tmp_path: Path, url: object,
                                                       kind: str) -> None:
    """FR-02, FR-05: refused at once with three parts; nothing is stored."""
    client = StubClient()
    service = local_service(tmp_path, {}, client)
    with pytest.raises(SubmissionRefused) as caught:
        service.submit(url)
    assert caught.value.kind == kind and caught.value.cause and caught.value.action
    assert not client.tokens and not service.store.repositories()


def test_an_inaccessible_repository_is_refused(tmp_path: Path) -> None:
    """FR-03: the check's structured error comes back; no repository row is written."""
    client = StubClient(failure=TdeaError(ErrorCode.NOT_FOUND, "Repository not found",
                                          "GitHub has no public repository a/b.",
                                          "Check the owner and repository name."))
    service = local_service(tmp_path, {}, client)
    with pytest.raises(SubmissionRefused) as caught:
        service.submit("https://github.com/a/b")
    assert caught.value.kind == "not_found"
    assert service.store.repositories() == []


def test_a_second_analysis_is_rejected_while_one_runs(fx_s, tmp_path: Path) -> None:
    """CON-03 / OI-04: the shared single-analysis lock rejects at once, with three parts."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path})
    with AnalysisLock(tmp_path / "lock"):
        with pytest.raises(SubmissionRefused) as caught:
            service.submit(FX_S_URL)
    assert caught.value.kind == "busy" and caught.value.action
    assert analysed(service, FX_S_URL).cached is False  # free again afterwards


def test_an_interrupted_analysis_leaves_no_commit_records(fx_s, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch
                                                          ) -> None:
    """NFR-08: a failure inside the final write rolls everything back."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path})
    calls = []

    def failing(commit_id, item):
        calls.append(commit_id)
        if len(calls) == 5:
            raise OSError("disk full")
        return original(commit_id, item)

    original = product_store._aggregate_row  # pylint: disable=protected-access
    monkeypatch.setattr(product_store, "_aggregate_row", failing)
    submission = analysed(service, FX_S_URL)
    assert service.store.repository(submission.repository_id).status == ERROR
    for table in ("commits", "file_metrics", "aggregate_metrics"):
        assert rows(service.store.path, f"SELECT count(*) FROM {table}") == [(0,)]


def test_a_run_left_running_is_marked_interrupted_on_restart(tmp_path: Path) -> None:
    """NFR-08: a repository still Running when the store reopens becomes an Error."""
    store = ProductStore(tmp_path / "tdea.db")
    from datetime import datetime, timezone  # pylint: disable=import-outside-toplevel
    repository_id = store.start("a/b", "https://github.com/a/b", "a", "b", 2,
                                datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert store.repository(repository_id).status == RUNNING
    store.close()
    service = local_service(tmp_path, {})
    row = service.store.repository(repository_id)
    assert row.status == ERROR and row.error_type == "Analysis interrupted"


def test_two_repositories_in_sequence_for_comparison(fx_s, tmp_path: Path) -> None:
    """FR-44: both checked first, then analysed one after the other into one store."""
    fx_err = make_fx_product.build_fx_err(tmp_path / "fx-err")
    client = StubClient()
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-s": fx_s.path,
                                              "tdea-fixtures/fx-err": fx_err.path}, client)
    first, second = service.submit_pair(FX_S_URL, FX_ERR_URL, token="t")
    assert service.wait(first.repository_id, 300) and service.wait(second.repository_id, 300)
    assert client.tokens == ["t", "t"]
    assert {service.store.repository(item.repository_id).status
            for item in (first, second)} == {COMPLETE}
    with pytest.raises(SubmissionRefused):
        service.submit_pair(FX_S_URL, FX_S_URL.upper().replace("HTTPS://GITHUB.COM",
                                                                "https://github.com"))


def test_a_cancelled_analysis_stops_and_stores_nothing(fx_s, tmp_path: Path) -> None:
    """SRS 4.1 Cancel: the run stops, the clone is deleted, a three-part error is kept."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-s": fx_s.path})
    submission = service.submit(FX_S_URL)
    assert service.cancel(submission.repository_id)
    assert service.wait(submission.repository_id, 300)
    repo = service.store.repository(submission.repository_id)
    assert repo.status == ERROR and repo.error_type == "Analysis cancelled"
    assert repo.error_cause and repo.error_action
    assert rows(service.store.path, "SELECT count(*) FROM commits") == [(0,)]
    assert not list(tmp_path.glob("tdea-clone-*"))  # FR-10
    (entry,) = [json.loads(line) for line in
                (tmp_path / AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
    assert entry["outcome"] == "Cancelled"
    assert not service.cancel(submission.repository_id)  # nothing runs any more
    assert analysed(service, FX_S_URL).cached is False  # the lock was released
