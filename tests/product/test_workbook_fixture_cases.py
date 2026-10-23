"""Workbook v1.2 cases that need the generated fixtures of make_fx_product (23 Oct).

The fixtures FX-M, FX-L, FX-100, FX-101, FX-102, FX-UP and FX-DOWN are built by
tests/fixtures/make_fx_product.py to the workbook's fixture table. Whether they are
accepted as the official fixtures is still the Product Owner's decision; the tests say
which case they run and at which level:

* TC-025 to TC-028 (FR-08 sampling) and TC-122 (FR-47 trend): end to end through
  AnalysisService, with only the FR-03 GitHub check stubbed.
* TC-033 and TC-067 (cancel at about 30 %): end to end. TC-033 also expects the status
  to read 'Cancelled', but FR-30 allows only Pending, Running, Complete and Error; TDEA
  shows Error with the error type "Analysis cancelled". That part is reported as a
  conflict, not as passed.
* TC-052 to TC-054 are browser cases. Here only their backend part is checked through
  the REST API (the 202 answer, other endpoints during a run, and the FR-18 values the
  engine emits); tests/browser/test_progress_view.py runs them in Chrome.

TC-126 (FX-XL, 500 commits, N = 3, at most 900 s) runs only with TDEA_SLOW_TESTS=1 and
is reported as skipped otherwise. The workbook's ACTUAL RESULT and STATUS columns are
not changed by these tests.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import make_fx_product
import pytest
from fixture_git import git_output
from product_support import analysed, local_service

from tdea.aggregator import results
from tdea.aggregator.analysis_service import AUDIT_FILE
from tdea.aggregator.product_models import ERROR
from tdea.aggregator.scoring import DECREASING, INCREASING
from tdea.api.app import create_app

PINNED_HEADS = {  # version 1 of each generated fixture
    "fx-m": "70a2017782d75258820c3171212f164f9f9d211f",
    "fx-up": "88820f6f5cca1f2c65f5a3df40a0466f444e3354",
    "fx-down": "c9224728187753238b99dfc81157ceedb596869a",
}


def url(key: str) -> str:
    """Return the placeholder GitHub URL of fixture ``key``."""
    return f"https://github.com/tdea-fixtures/{key}"


@pytest.fixture(name="counts", scope="module")
def _counts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build FX-100, FX-101 and FX-102 once for this module."""
    root = tmp_path_factory.mktemp("fxn")
    return {key: make_fx_product.BUILDERS[key](root / key).path
            for key in ("fx-100", "fx-101", "fx-102")}


@pytest.mark.parametrize("key, commits", [("fx-100", 100), ("fx-101", 101), ("fx-102", 102)])
def test_fx_100_101_102_have_exactly_their_commit_counts(counts, key: str, commits: int) -> None:
    """The workbook's sampling-boundary fixtures: exactly 100, 101 and 102 commits."""
    assert len(git_output(counts[key], "rev-list", "HEAD").split()) == commits


def test_generated_fixtures_are_identical_on_every_build(tmp_path: Path) -> None:
    """Each builder gives the pinned version-1 HEAD, so every run uses the same history."""
    for key, head in PINNED_HEADS.items():
        assert make_fx_product.BUILDERS[key](tmp_path / key).shas[-1] == head, key


def history_positions(path: Path, service, repository_id: int) -> list[int]:
    """Return the 1-based history position of every analysed commit."""
    shas = git_output(path, "rev-list", "--reverse", "HEAD").split()
    stored = [commit.sha for commit, _agg in service.store.commits(repository_id)]
    return [shas.index(sha) + 1 for sha in stored]


@pytest.fixture(name="fx_100_run", scope="module")
def _fx_100_run(counts, tmp_path_factory: pytest.TempPathFactory):
    """Analyse FX-100 once, recording every FR-18 value the engine emits (TC-025, TC-054)."""
    service = local_service(tmp_path_factory.mktemp("fx100"),
                            {"tdea-fixtures/fx-100": counts["fx-100"]})
    emitted: list[int] = []
    original = service._progress_callback  # pylint: disable=protected-access

    def recording(repository_id: int):
        update = original(repository_id)

        def record(percent: int, processed: int, total: int, sha: str) -> None:
            emitted.append(percent)
            update(percent, processed, total, sha)
        return record

    service._progress_callback = recording  # pylint: disable=protected-access
    submission = analysed(service, url("fx-100"))
    return service, submission.repository_id, emitted


def test_tc025_exactly_100_commits_are_not_sampled(counts, fx_100_run) -> None:
    """TC-025: FX-100 (100 commits) is analysed in full."""
    service, repository_id, _emitted = fx_100_run
    assert history_positions(counts["fx-100"], service, repository_id) == list(range(1, 101))


def test_tc026_101_commits_with_the_default_n(counts, tmp_path: Path) -> None:
    """TC-026: FX-101, default N = 2: 51 commits (#1, #3 ... #101), first and HEAD included."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-101": counts["fx-101"]})
    submission = analysed(service, url("fx-101"))
    assert history_positions(counts["fx-101"], service, submission.repository_id) == \
        list(range(1, 102, 2))


def test_tc027_head_is_added_when_n_misses_it(counts, tmp_path: Path) -> None:
    """TC-027: FX-102, N = 4: #1, #5 ... #101 (26 commits) plus HEAD #102, 27 in all."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-102": counts["fx-102"]})
    submission = analysed(service, url("fx-102"), sampling_n=4)
    positions = history_positions(counts["fx-102"], service, submission.repository_id)
    assert positions == [*range(1, 102, 4), 102] and len(positions) == 27


def test_tc028_n_1_analyses_every_commit(counts, tmp_path: Path) -> None:
    """TC-028: FX-101, N = 1: all 101 commits."""
    service = local_service(tmp_path, {"tdea-fixtures/fx-101": counts["fx-101"]})
    submission = analysed(service, url("fx-101"), sampling_n=1)
    assert history_positions(counts["fx-101"], service, submission.repository_id) == \
        list(range(1, 102))


def test_tc122_fx_up_is_increasing_and_fx_down_is_decreasing(tmp_path: Path) -> None:
    """TC-122: compared, FX-UP is labelled 'Increasing' and FX-DOWN 'Decreasing'."""
    up = make_fx_product.build_fx_up(tmp_path / "fx-up")
    down = make_fx_product.build_fx_down(tmp_path / "fx-down")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-up": up.path,
                                              "tdea-fixtures/fx-down": down.path})
    first, second = service.submit_pair(url("fx-up"), url("fx-down"))
    assert service.wait(first.repository_id, 300) and service.wait(second.repository_id, 300)
    sides = results.comparison(service.store, first.repository_id,
                               second.repository_id)["repositories"]
    assert [side["summary"]["trend"] for side in sides] == [INCREASING, DECREASING]
    # The fixtures do what the workbook says: debt rises (falls) at every commit.
    up_deltas = [point["debt_delta"] for point in sides[0]["timeline"][1:]]
    down_deltas = [point["debt_delta"] for point in sides[1]["timeline"][1:]]
    assert all(delta > 0 for delta in up_deltas) and len(up_deltas) == 11
    assert all(delta < 0 for delta in down_deltas) and len(down_deltas) == 11


@pytest.fixture(name="fx_m", scope="module")
def _fx_m(tmp_path_factory: pytest.TempPathFactory) -> make_fx_product.BuiltFixture:
    """Build FX-M once for this module."""
    return make_fx_product.build_fx_m(tmp_path_factory.mktemp("fxm") / "fx-m")


def test_tc033_tc067_cancel_at_30_percent(fx_m, tmp_path: Path) -> None:
    """TC-033: the clone is removed after Cancel at about 30 %. TC-067: audited, processed
    < requested.

    TC-033's "status shows 'Cancelled'" is not asserted as written: FR-30 allows only
    Pending, Running, Complete or Error, so the repository shows Error with the error type
    "Analysis cancelled" (reported as a workbook/SRS conflict).
    """
    service = local_service(tmp_path, {"tdea-fixtures/fx-m": fx_m.path})
    submission = service.submit(url("fx-m"))
    deadline = time.monotonic() + 300
    while True:
        progress = service.progress(submission.repository_id)
        assert progress is not None and time.monotonic() < deadline, "the run ended first"
        if progress.percent >= 30:
            break
        time.sleep(0.02)
    assert service.cancel(submission.repository_id)
    assert service.wait(submission.repository_id, 300)
    assert not list(tmp_path.glob("tdea-clone-*"))  # TC-033, FR-10
    repo = service.store.repository(submission.repository_id)
    assert repo.status == ERROR and repo.error_type == "Analysis cancelled"
    entry = json.loads((tmp_path / AUDIT_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert entry["outcome"] == "Cancelled" and entry["url"] == url("fx-m")
    assert entry["commits_requested"] == 50
    assert 15 <= entry["commits_processed"] < entry["commits_requested"]  # TC-067
    assert entry["started_at"] and entry["completed_at"] and entry["file_errors"] == 0


@pytest.fixture(name="fx_l", scope="module")
def _fx_l(tmp_path_factory: pytest.TempPathFactory) -> make_fx_product.BuiltFixture:
    """Build FX-L (200 commits) once for this module."""
    return make_fx_product.BUILDERS["fx-l"](tmp_path_factory.mktemp("fxl") / "fx-l")


def test_fx_l_has_200_commits(fx_l) -> None:
    """FX-L: a Python repository with about 200 commits (exactly 200)."""
    assert len(fx_l.shas) == 200
    assert len(git_output(fx_l.path, "rev-list", "HEAD").split()) == 200


def test_tc052_tc053_backend_returns_at_once_and_stays_responsive(
        fx_l, tmp_path: Path) -> None:
    """TC-052 and TC-053, backend part only (the browser part is not executed here).

    POST /api/analyses for FX-L answers 202 within 2 seconds while the analysis runs in
    the background; during the run, the repository list and a previously analysed
    repository's endpoints answer 200, and the running analysis still completes.
    """
    fx_s = make_fx_product.build_fx_s(tmp_path / "fx-s")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-l": fx_l.path,
                                              "tdea-fixtures/fx-s": fx_s.path})
    earlier = analysed(service, url("fx-s"))
    client = create_app(service).test_client()
    started = time.monotonic()
    response = client.post("/api/analyses", json={"url": url("fx-l")})
    elapsed = time.monotonic() - started
    assert response.status_code == 202 and elapsed < 2.0, f"{elapsed:.2f} s"
    running = response.json["repository_id"]
    assert service.progress(running) is not None  # still running in the background
    for path in ("/api/repositories", f"/api/repositories/{earlier.repository_id}",
                 f"/api/repositories/{earlier.repository_id}/commits",
                 f"/api/repositories/{running}"):
        assert client.get(path).status_code == 200, path
    assert service.wait(running, 600)
    assert service.store.repository(running).status == "Complete"


def test_tc054_backend_progress_values_for_fx_100(fx_100_run) -> None:
    """TC-054, backend part: every FR-18 value the engine emits for FX-100.

    At least 10 updates, integers 0-100, never decreasing, ending at 100. The values the
    frontend receives (the case as written) are checked by the browser tests.
    """
    _service, _repository_id, emitted = fx_100_run
    assert len(emitted) >= 10
    assert all(isinstance(value, int) and 0 <= value <= 100 for value in emitted)
    assert emitted == sorted(emitted) and emitted[-1] == 100


@pytest.mark.skipif(os.environ.get("TDEA_SLOW_TESTS") != "1",
                    reason="TC-126 runs a 500-commit analysis: set TDEA_SLOW_TESTS=1")
def test_tc126_fx_xl_with_n_3_finishes_within_900_seconds(tmp_path: Path) -> None:
    """TC-126 (NFR-04): FX-XL (500 commits), N = 3, completes in at most 900 s, no crash."""
    fx_xl = make_fx_product.BUILDERS["fx-xl"](tmp_path / "fx-xl")
    assert len(fx_xl.shas) == 500
    service = local_service(tmp_path / "ws", {"tdea-fixtures/fx-xl": fx_xl.path})
    started = time.monotonic()
    submission = service.submit(url("fx-xl"), sampling_n=3)
    assert service.wait(submission.repository_id, 1200)
    elapsed = time.monotonic() - started
    assert service.store.repository(submission.repository_id).status == "Complete"
    entry = json.loads((tmp_path / "ws" / AUDIT_FILE).read_text(encoding="utf-8")
                       .splitlines()[-1])
    assert entry["commits_requested"] == 168  # #1, #4 ... #499 (167) and HEAD #500
    assert elapsed <= 900, f"{elapsed:.0f} s"
