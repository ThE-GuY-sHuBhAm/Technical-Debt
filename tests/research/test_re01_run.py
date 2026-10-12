"""RE-01 and RE-02 through ``tdea research run`` and ``status`` (FR-48, FR-49, FR-52, Day 6).

BLOCKED as acceptance checks: RE-01 needs a run of FX-RG1 and FX-S, and every real run
is refused while CL-03 is incomplete (decision 1; strict expected failure below); FX-S
does not exist yet. What runs here is the same command line and the same code path with
SYNTHETIC — not CL-03 doubles for provenance and the package filter (run_support), over
FX-RG1 v2 and FX-PAIRS cloned from local folders: mechanics evidence only. The
placeholder URLs are never contacted.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1
import make_fx_rg1_v2
import pytest
from run_setup import Built
from run_support import (FX_PAIRS_URL, FX_RG1_URL, FX_RG1_WINDOWS, PINNED_FACTS, Workspace,
                         dump, four_units, rows, run_file_text)

from tdea.aggregator.research_service import ResearchService
from tdea.analyzer.research.engine import ResearchEngine
from tdea.analyzer.research.profile import ResearchProfile
from tdea.api.cli import EXIT_INVALID_INPUT, EXIT_OK, EXIT_REFUSED, main

UTC = timezone.utc


class CountingProfile(ResearchProfile):
    """The real profile, counting every lint request and every fresh (uncached) lint."""

    def __init__(self) -> None:
        """Start with no lints."""
        super().__init__()
        self.requests: list[tuple[str | None, str]] = []
        self.fresh: list[str] = []

    def lint(self, content: bytes, file_name: str, *, blob_sha: str | None = None,
             cache=None):
        """Count the request, then lint as usual."""
        self.requests.append((blob_sha, file_name))
        return super().lint(content, file_name, blob_sha=blob_sha, cache=cache)

    def _lint_fresh(self, content: bytes, file_name: str):
        """Count a lint the cache did not answer."""
        self.fresh.append(file_name)
        return super()._lint_fresh(content, file_name)


def run(workspace: Workspace, text: str, service: ResearchService | None = None) -> int:
    """Write the run file and run it through the command line."""
    path = workspace.run_file(text)
    return main(["research", "run", str(path)],
                service=service if service is not None else workspace.service())


def git_log(path: Path, head: str = "HEAD") -> list[list[str]]:
    """Return git log --first-parent from root to ``head``: SHA, parents, author, committer."""
    out = subprocess.run(["git", "log", "--first-parent", "--reverse",
                          "--format=%H|%P|%aI|%cI", head], cwd=path, capture_output=True,
                         text=True, check=True).stdout
    return [line.split("|") for line in out.splitlines()]


def utc(text: str) -> str:
    """Return a git ISO time in UTC, as research.db stores it."""
    return datetime.fromisoformat(text).astimezone(UTC).isoformat()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: RE-01 step 1 needs a real run of FX-RG1 and FX-S; a run "
                          "is refused while CL-03 is incomplete (Day 6 decision 1)")
def test_re01_step1_as_written(built: Built, tmp_path: Path) -> None:
    """RE-01 step 1 with the real engine: refused with exit 3, so 4 units cannot exit 0."""
    workspace = built.workspace(tmp_path / "ws")
    service = ResearchService(ResearchEngine(), paths=workspace.paths(),
                              environment=PINNED_FACTS)
    assert run(workspace, four_units(), service) == EXIT_OK


def test_synthetic_four_units_exit_0_and_the_identifier_comes_first(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """SYNTHETIC — not CL-03: 2 repositories x 2 windows give 4 complete units and exit 0."""
    monkeypatch.delenv("TDEA_GITHUB_TOKEN", raising=False)  # no PAT: the check gets None
    workspace = built.workspace(tmp_path / "ws")
    assert main(["research", "validate", str(workspace.run_file(four_units()))]) == EXIT_OK
    capsys.readouterr()
    assert run(workspace, four_units()) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    run_id = out[0].removeprefix("Run identifier: ")
    assert len(run_id) == 12 and out[0] == f"Run identifier: {run_id}"
    assert out[1] == f"Starting run {run_id}: 4 units."
    assert [line.split(":")[0] for line in out[2:6]] == [
        "Unit 1 of 4 (tdea-fixtures/fx-rg1, 2026-01-01 to 2026-04-07)",
        "Unit 2 of 4 (tdea-fixtures/fx-rg1, 2026-04-07 to 2026-08-01)",
        "Unit 3 of 4 (tdea-fixtures/fx-pairs, 2026-05-01 to 2026-05-03)",
        "Unit 4 of 4 (tdea-fixtures/fx-pairs, 2026-05-03 to 2026-06-01)"]
    assert out[6] == f"Run {run_id} complete: 4 of 4 units."
    assert rows(workspace.store, "SELECT run_id, status FROM run") == [(run_id, "complete")]
    assert rows(workspace.store, "SELECT count(*), min(status), max(status) FROM unit") == [
        (4, "complete", "complete")]
    assert not list(workspace.clones.iterdir())  # FR-10: clones deleted
    assert workspace.access.tokens == [None, None]


def test_synthetic_re02_history_and_metadata_are_stored(built: Built, tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: RE-02 steps 2 to 4 on the stored FX-RG1 v2 history."""
    workspace = built.workspace(tmp_path / "ws")
    assert run(workspace, four_units()) == EXIT_OK
    shas = built.fx_rg1.shas
    stored = rows(workspace.store,
                  "SELECT c.sha, c.parent_shas, c.authored_at, c.committed_at, c.is_merge "
                  "FROM 'commit' c JOIN repository r USING (repository_id) WHERE r.url = ? "
                  "ORDER BY c.first_parent_position", FX_RG1_URL)
    expected = [entry for entry in git_log(built.fx_rg1.path) if entry[0] != shas["c_fix"]]
    assert [(sha, parents, author, committer) for sha, parents, author, committer, _ in stored] \
        == [(sha, '[' + ",".join(f'"{p}"' for p in parents.split()) + ']', utc(author),
             utc(committer)) for sha, parents, author, committer in expected]
    merge = [row for row in stored if row[0] == shas["merge"]][0]
    assert merge[4] == 1 and merge[1].count(",") == 1
    (repository,) = rows(workspace.store, "SELECT head_sha, clone_time, cutoff, default_branch "
                                          "FROM repository WHERE url = ?", FX_RG1_URL)
    assert repository == (shas["c_fix"], "2026-10-12T09:00:00+00:00",
                          "2026-10-11T00:00:00+00:00", "main")
    (message,) = rows(workspace.store, "SELECT message FROM 'commit' WHERE sha = ?",
                      shas["trailers"])
    assert "Co-authored-by: id:" in message[0] and "@" not in message[0]
    window_1 = {sha for (sha,) in rows(
        workspace.store, "SELECT c.sha FROM 'commit' c JOIN unit u USING (unit_id) "
        "WHERE u.window_start = '2026-01-01T00:00:00+00:00'")}
    assert window_1 == {shas[label] for label in make_fx_rg1.FIRST_PARENT}
    raw = workspace.store.read_bytes()
    for identity in (make_fx_rg1.ASHA, make_fx_rg1.RAVI, make_fx_rg1.JANE,
                     make_fx_rg1.DEPENDENCY_BOT, make_fx_rg1.AI_COMMITTER):
        assert identity.email.encode() not in raw and identity.name.encode() not in raw


def test_synthetic_run_metadata_records_every_fr52_field(built: Built, tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: the run row holds its FR-52 metadata; the doubles are named."""
    workspace = built.workspace(tmp_path / "ws")
    assert run(workspace, four_units()) == EXIT_OK
    (row,) = rows(workspace.store, "SELECT * FROM run")
    assert None not in row  # every FR-52 field, the end time included, is recorded
    (lists,) = rows(workspace.store, "SELECT rule_list_versions, prepare_module_version, "
                                     "rename_threshold, time_limit_seconds, cutoff FROM run")
    assert "SYNTHETIC — not CL-03: classifier" in lists[0]
    assert "SYNTHETIC — not CL-03: bulk lists" in lists[0] and lists[1:] == (
        "1", 50, 120.0, "2026-10-11T00:00:00+00:00")


def test_synthetic_re01_step6_a_completed_run_does_nothing(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SYNTHETIC — not CL-03: starting the completed run again exits 0 without work."""
    workspace = built.workspace(tmp_path / "ws")
    assert run(workspace, four_units()) == EXIT_OK
    before = dump(workspace.store)
    capsys.readouterr()
    assert run(workspace, four_units()) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out[1] == f"Run {out[0].removeprefix('Run identifier: ')} is already complete; " \
                     "nothing to do."
    assert dump(workspace.store) == before
    assert len(workspace.access.tokens) == 2  # no accessibility check, no clone the 2nd time


def test_synthetic_re01_step4_a_sha_list_limits_analysis(built: Built, tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: window 2 limited to A's and D's commits introduces only A and D."""
    shas = built.fx_rg1.shas
    window = (f"{{start: 2026-04-07, end: 2026-08-01, commits: "
              f"[{shas['d_intro']}, {shas['a_intro']}]}}")
    workspace = built.workspace(tmp_path / "ws")
    text = run_file_text([(FX_RG1_URL, (FX_RG1_WINDOWS[0], window), None)])
    assert run(workspace, text) == EXIT_OK
    selected = rows(workspace.store, "SELECT c.sha FROM 'commit' c JOIN unit u USING (unit_id) "
                                     "WHERE u.window_start LIKE '2026-04-07%'")
    assert sorted(sha for (sha,) in selected) == sorted([shas["a_intro"], shas["d_intro"]])
    paths = rows(workspace.store, "SELECT path FROM debt_item ORDER BY path")
    assert paths == [("pkg/tracked_a.py",), ("pkg/tracked_d.py",)]
    (hashed,) = rows(workspace.store, "SELECT sha_list_hash FROM unit "
                                      "WHERE window_start LIKE '2026-04-07%'")
    assert hashed[0] is not None and len(hashed[0]) == 64


def test_synthetic_a_sha_outside_its_window_exits_1_after_the_clone_before_analysis(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-48: a listed SHA outside its window is rejected after the clone, before any unit."""
    shas = built.fx_rg1.shas
    window = f"{{start: 2026-04-07, end: 2026-08-01, commits: [{shas['root']}]}}"
    workspace = built.workspace(tmp_path / "ws")
    text = run_file_text([(FX_RG1_URL, (FX_RG1_WINDOWS[0], window), None)])
    assert run(workspace, text) == EXIT_INVALID_INPUT
    err = capsys.readouterr().err
    assert err.startswith("Error: Invalid commit list\nCause: https://github.com/tdea-fixtures/"
                          f"fx-rg1: Listed commits are outside the window: {shas['root']}.")
    assert rows(workspace.store, "SELECT count(*) FROM unit") == [(0,)]
    assert rows(workspace.store, "SELECT count(*) FROM 'commit'") == [(0,)]
    assert not list(workspace.clones.iterdir())


def test_synthetic_re02_step5_pinned_head_and_earlier_cutoff(built: Built,
                                                             tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: history up to the pinned HEAD; later commits ignored and counted."""
    shas = built.fx_rg1.shas
    workspace = built.workspace(tmp_path / "ws")
    text = run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS[:1], shas["latest"])],
                         cutoff="2026-03-20T00:00:00Z")
    assert run(workspace, text) == EXIT_OK
    (repository,) = rows(workspace.store, "SELECT head_sha, cutoff FROM repository")
    assert repository == (shas["latest"], "2026-03-20T00:00:00+00:00")
    stored = [sha for (sha,) in rows(workspace.store, "SELECT sha FROM 'commit' "
                                                      "ORDER BY first_parent_position")]
    log = [sha for sha, *_ in git_log(built.fx_rg1.path, shas["latest"])]
    assert stored == log[:-3]  # merge, signed_off and latest are dated after the cut-off
    assert rows(workspace.store, "SELECT selected_commits, commits_after_cutoff FROM unit") \
        == [(len(log) - 3, 3)]


def test_synthetic_a_pinned_head_missing_from_the_clone_exits_3(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plan exit condition: a missing pinned HEAD gives exit 3 ("history changed")."""
    workspace = built.workspace(tmp_path / "ws")
    text = run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS, "1" * 40)])
    assert run(workspace, text) == EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.startswith("Error: History changed\nCause: A fresh clone of "
                          f"{FX_RG1_URL} does not contain commit {'1' * 40}")
    assert rows(workspace.store, "SELECT count(*) FROM unit") == [(0,)]
    assert not list(workspace.clones.iterdir())


def test_synthetic_re06_step4_w_9_is_a_new_run_with_cache_hits(built: Built,
                                                               tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: W = 9 gets a new identifier and recomputes, linting nothing anew."""
    workspace = built.workspace(tmp_path / "ws")
    assert run(workspace, four_units()) == EXIT_OK
    profile = CountingProfile()
    assert run(workspace, four_units().replace("w_months: 6", "w_months: 9"),
               workspace.service(profile=profile)) == EXIT_OK
    assert rows(workspace.store, "SELECT count(DISTINCT run_id), count(*) FROM unit") == [(2, 8)]
    # Every version the W = 6 run linted is a cache hit; the one fresh lint is B's version
    # at its fix, which W = 6 never reached.
    assert len(profile.requests) > 10 and profile.fresh == ["tracked_b.py"]
    tracked_b = rows(workspace.store,
                     "SELECT r.w_months, d.censoring_reason, c.sha FROM debt_item d "
                     "JOIN unit u USING (unit_id) JOIN run r USING (run_id) "
                     "LEFT JOIN 'commit' c ON c.commit_id = d.c1_commit_id "
                     "WHERE d.path = 'pkg/tracked_b.py' ORDER BY r.w_months")
    assert tracked_b == [(6, "window", None), (9, None, built.fx_rg1.shas["b_fix"])]


def test_synthetic_the_pat_reaches_the_check_only_and_is_stored_nowhere(
    built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], canary_token: str, find_canary
) -> None:
    """NFR-19: TDEA_GITHUB_TOKEN reaches the FR-03 check and no file, store or output."""
    workspace = built.workspace(tmp_path / "ws")
    monkeypatch.setenv("TDEA_GITHUB_TOKEN", canary_token)
    assert run(workspace, four_units()) == EXIT_OK
    assert workspace.access.tokens == [canary_token, canary_token]
    output = capsys.readouterr()
    assert canary_token not in output.out + output.err
    assert find_canary(workspace.root, workspace.clones) == []


def test_synthetic_status_lists_runs_and_units_read_only(
    built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """status lists the run and its units, planned ones included, and changes no byte."""
    workspace = built.workspace(tmp_path / "ws")
    text = four_units()
    assert run(workspace, run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS[:1], None)])) == EXIT_OK
    with pytest.raises(SimulatedCrash):
        run(workspace, text, crashing_after_first_unit(workspace))
    before = (workspace.store.read_bytes(), sorted(p.name for p in workspace.root.rglob("*")))
    capsys.readouterr()
    assert main(["research", "status", str(workspace.run_file(text))],
                service=workspace.service()) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert (workspace.store.read_bytes(), sorted(p.name for p in workspace.root.rglob("*"))) \
        == before
    assert out[0] == f"Research store: {workspace.store.resolve()}"
    runs = [number for number, line in enumerate(out) if line.startswith("Run ")]
    assert len(runs) == 2
    finished, partial = sorted(runs, key=lambda number: "running" in out[number])
    assert ": complete, started" in out[finished]
    assert out[finished].endswith("; 1 of 1 unit complete.")
    assert ": running, started" in out[partial]
    assert out[partial].endswith("; 1 of 4 units complete.")
    units = out[partial + 1:partial + 5]
    assert units[0].startswith(f"  {FX_RG1_URL}, 2026-01-01 to 2026-04-07: complete at ")
    assert units[0].endswith("; 15 selected commits, 0 introductions, 0 lint failures, "
                             "0 commits after the cut-off.")
    assert units[1:] == [f"  {FX_RG1_URL}, 2026-04-07 to 2026-08-01: planned.",
                         f"  {FX_PAIRS_URL}, 2026-05-01 to 2026-05-03: planned.",
                         f"  {FX_PAIRS_URL}, 2026-05-03 to 2026-06-01: planned."]


class SimulatedCrash(Exception):
    """A failure no layer handles, standing in for a crash between units."""


def crashing_after_first_unit(workspace: Workspace) -> ResearchService:
    """Return a service whose engine fails when it starts the second unit."""
    service = workspace.service()
    engine = service._engine  # pylint: disable=protected-access
    original = engine.run_rg1_unit
    calls = []

    def run_rg1_unit(session, window, w_months):
        """Run the first unit, then fail as a crash would."""
        calls.append(window)
        if len(calls) > 1:
            raise SimulatedCrash
        return original(session, window, w_months)

    engine.run_rg1_unit = run_rg1_unit
    return service


def test_fx_rg1_v2_keeps_every_v1_sha(built: Built) -> None:
    """Decision 6: v2 is v1 plus new commits; v1's builder and SHAs are unchanged."""
    assert make_fx_rg1.VERSION == "1" and make_fx_rg1_v2.VERSION == "2"
    assert built.fx_rg1.shas.items() >= built.fx_rg1.v1_shas.items()
    assert set(built.fx_rg1.v1_shas) == set(make_fx_rg1.FIRST_PARENT) | set(
        make_fx_rg1.SIDE_BRANCH)
    log = [sha for sha, *_ in git_log(built.fx_rg1.path)]
    assert log == built.fx_rg1.first_parent_shas()
    assert log.index(built.fx_rg1.shas["latest"]) == len(make_fx_rg1.FIRST_PARENT) - 1
