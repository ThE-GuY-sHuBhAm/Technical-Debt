"""ResearchJob: resume, interruption, rollback, duplicates and clean-up (NFR-25, NFR-08, FR-10).

SYNTHETIC — not CL-03 (run_support) for provenance and the package filter; everything
else is real: git, the research profile, the lint cache, research.db and the lock.

* A real kill: a child process runs until unit 2 has written its records and is about to
  commit, and is killed there (SIGKILL on POSIX, TerminateProcess on Windows). The
  restart must leave unit 1's records and completion time unchanged, lint none of unit
  1's files, find no record of unit 2 (NFR-25's acceptance, as mechanics), and delete
  the clone the killed process left (FR-10).
* In-process failures inside and after a unit's transaction show the rollback and the
  resume, and the duplicate guard refuses to store a unit twice.
* A clone failure stops the run with exit 4 (FR-48, Freeze revision 116), keeping
  earlier units, and running again resumes (RE-01 step 7). Failures without an agreed
  exit code (a binary diff on a tracked file, a failed accessibility check) stop the run,
  keep earlier units and are printed in three parts with the undecided note (decision 3).
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import make_fx_rg1
import pytest
from fixture_git import FixtureRepo
from run_setup import Built
from run_support import (FX_RG1_PACKAGES, FX_RG1_URL, FX_RG1_WINDOWS, LocalAccess,
                         SyntheticBulkRules, SyntheticClassifier, SyntheticPackages, Workspace,
                         dump, four_units, rows, run_file_text)
from sqlalchemy.exc import IntegrityError, OperationalError

from tdea.aggregator.lock import AnalysisLock
from tdea.aggregator.research_job import ResearchJob, RunObserver, plan_units
from tdea.aggregator.research_service import ResearchRunError, ResearchService
from tdea.aggregator.research_store import ResearchStore
from tdea.analyzer.research.engine import GitHubAccess, Rg1Rules
from tdea.analyzer.research.profile import ResearchProfile
from tdea.analyzer.research.rg1 import unit_run
from tdea.analyzer.research.rg1.provenance import MatchingRulesUnresolved, UnresolvedMatcher
from tdea.api import cli
from tdea.api.cli import EXIT_OK, EXIT_REFUSED, main
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.github_client import GitHubClient
from tdea.git_handler.local_repository import remove_stale_clones
from tdea.git_handler.url_validator import RepositoryRef

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures"
UNIT_1 = "SELECT unit_id, run_id, repository_id, window_start, window_end, sha_list_hash, " \
         "status, completed_at, selected_commits, introductions, lint_failures, " \
         "commits_after_cutoff FROM unit WHERE window_start LIKE '2026-01-01%'"


class Crash(Exception):
    """A failure no layer handles, standing in for a crash."""


class FailAt(RunObserver):
    """Raises Crash when a chosen unit reaches a chosen point."""

    def __init__(self, number: int, point: str) -> None:
        """Fail in unit ``number`` at ``point`` ("written" or "completed")."""
        self.number = number
        self.point = point

    def unit_written(self, unit, total: int) -> None:
        """Fail inside the transaction, after every record was added."""
        if self.point == "written" and unit.number == self.number:
            raise Crash

    def unit_completed(self, unit, total: int, counts) -> None:
        """Fail after the transaction committed."""
        if self.point == "completed" and unit.number == self.number:
            raise Crash


class CountingProfile(ResearchProfile):
    """The real profile, recording the blob of every lint request."""

    def __init__(self) -> None:
        """Start with no requests."""
        super().__init__()
        self.blobs: list[str | None] = []

    def lint(self, content: bytes, file_name: str, *, blob_sha: str | None = None,
             cache=None):
        """Record the blob, then lint as usual."""
        self.blobs.append(blob_sha)
        return super().lint(content, file_name, blob_sha=blob_sha, cache=cache)


def start(workspace: Workspace, text: str, observer: RunObserver | None = None,
          profile: ResearchProfile | None = None) -> None:
    """Run through the service, letting a Crash escape."""
    workspace.service(profile=profile).run(workspace.run_file(text), observer=observer)


def unit_1_records(store: Path) -> dict[str, list[tuple]]:
    """Return unit 1's row, debt items and selected commits."""
    return {"unit": rows(store, UNIT_1),
            "items": rows(store, "SELECT * FROM debt_item d JOIN unit u USING (unit_id) "
                                 "WHERE u.window_start LIKE '2026-01-01%' ORDER BY item_id"),
            "commits": rows(store, "SELECT c.* FROM 'commit' c JOIN unit u USING (unit_id) "
                                   "WHERE u.window_start LIKE '2026-01-01%' ORDER BY sha")}


def unit_1_blobs(store: Path) -> set[str]:
    """Return the blobs of unit 1's selected commits' package files, both sides."""
    found = rows(store, "SELECT f.old_blob, f.new_blob FROM file_change f "
                        "JOIN 'commit' c USING (commit_id) JOIN unit u USING (unit_id) "
                        "WHERE u.window_start LIKE '2026-01-01%' AND f.is_package")
    return {blob for pair in found for blob in pair if blob}


def test_plan_units_follows_the_run_file(built: Built, tmp_path: Path) -> None:
    """Units are repositories x windows, numbered in run-file order (step 3)."""
    workspace = built.workspace(tmp_path / "ws")
    run_file = workspace.service().validate(workspace.run_file(four_units()))
    units = plan_units(run_file)
    assert [(unit.number, unit.repository.name, unit.window.start.date().isoformat())
            for unit in units] == [(1, "fx-rg1", "2026-01-01"), (2, "fx-rg1", "2026-04-07"),
                                   (3, "fx-pairs", "2026-05-01"), (4, "fx-pairs", "2026-05-03")]
    assert all(unit.sha_list_hash is None for unit in units)


def test_a_kill_inside_a_unit_leaves_no_partial_records_and_the_restart_skips_unit_1(
    built: Built, tmp_path: Path
) -> None:
    """NFR-25 with a real kill: unit 1 kept unchanged and not re-linted; unit 2 left no trace."""
    workspace = built.workspace(tmp_path / "ws")
    run_file = workspace.run_file(four_units())
    marker = tmp_path / "written"
    assert workspace.clone_entries() == set()
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(FIXTURES), os.environ.get("PYTHONPATH", "")])}
    child = subprocess.Popen([sys.executable, str(HERE / "kill_helper.py"), str(workspace.root),
                              str(built.fx_rg1.path), str(built.fx_pairs), str(run_file),
                              str(marker), "2"], env=environment)
    try:
        deadline = time.monotonic() + 300
        while not marker.exists():
            assert child.poll() is None, "the child stopped before unit 2's transaction"
            assert time.monotonic() < deadline, "the child never reached unit 2"
            time.sleep(0.1)
        (active,) = workspace.clone_entries()  # the paused child's clone, in use
        with pytest.raises(ResearchRunError) as refused:  # a second run meanwhile
            start(workspace, four_units())
        assert refused.value.exit_code == EXIT_REFUSED
        assert workspace.clone_entries() == {active} and (active / ".git").is_dir()
    finally:
        child.kill()
        child.wait(timeout=60)
    after_kill = unit_1_records(workspace.store)  # opening rolls back the hot journal
    assert rows(workspace.store, "SELECT count(*), max(status) FROM unit") == [(1, "complete")]
    assert rows(workspace.store, "SELECT count(*) FROM debt_item") == [
        (len(after_kill["items"]),)]
    assert rows(workspace.store, "SELECT count(*) FROM 'commit' WHERE unit_id IS NOT NULL") \
        == [(len(after_kill["commits"]),)]
    assert rows(workspace.store, "SELECT status FROM run") == [("running",)]
    (killed_clone,) = workspace.clone_entries()  # a killed process cannot clean up
    assert killed_clone.name.startswith("tdea-clone-") and (killed_clone / ".git").is_dir()

    profile = CountingProfile()
    for _attempt in range(100):  # Windows may release a killed process's lock a moment late
        try:
            start(workspace, four_units(), profile=profile)
            break
        except ResearchRunError as error:
            assert error.error_type == "Another analysis is running"
            time.sleep(0.1)
    assert unit_1_records(workspace.store) == after_kill
    assert profile.blobs and not unit_1_blobs(workspace.store) & set(profile.blobs)
    assert rows(workspace.store, "SELECT count(*), min(status) FROM unit") == [(4, "complete")]
    assert rows(workspace.store, "SELECT status FROM run") == [("complete",)]
    assert workspace.clone_entries() == set()  # FR-10: the restart removed the leftover


def test_a_failure_inside_the_transaction_rolls_the_whole_unit_back(built: Built,
                                                                     tmp_path: Path) -> None:
    """NFR-08: unit 3 (FX-PAIRS's first unit) fails before commit: no unit, item or history."""
    workspace = built.workspace(tmp_path / "ws")
    with pytest.raises(Crash):
        start(workspace, four_units(), FailAt(3, "written"))
    assert rows(workspace.store, "SELECT count(*) FROM unit") == [(2,)]
    assert rows(workspace.store, "SELECT url FROM repository") == [(FX_RG1_URL,)]
    assert rows(workspace.store, "SELECT count(*) FROM debt_item d JOIN unit u USING (unit_id) "
                                 "JOIN repository r USING (repository_id) "
                                 "WHERE r.url <> ?", FX_RG1_URL) == [(0,)]
    assert not list(workspace.clones.iterdir())  # FR-10 also on failure
    start(workspace, four_units())
    assert rows(workspace.store, "SELECT count(*) FROM unit") == [(4,)]


def test_a_crash_after_a_commit_resumes_without_recomputing(built: Built, tmp_path: Path,
                                                            capsys) -> None:
    """A crash after unit 1 committed: the restart skips it and its records stay identical."""
    workspace = built.workspace(tmp_path / "ws")
    with pytest.raises(Crash):
        start(workspace, four_units(), FailAt(1, "completed"))
    kept = unit_1_records(workspace.store)
    capsys.readouterr()
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out[1].endswith(": 1 of 4 units already complete.")
    assert out[2] == ("Unit 1 of 4 (tdea-fixtures/fx-rg1, 2026-01-01 to 2026-04-07): "
                      "already complete, skipped.")
    assert unit_1_records(workspace.store) == kept


def test_a_resumed_run_equals_an_uninterrupted_one(built: Built, tmp_path: Path) -> None:
    """Interrupting and resuming gives the same records as one run, times and ids aside."""
    whole, broken = built.workspace(tmp_path / "whole"), built.workspace(tmp_path / "broken")
    start(whole, four_units())
    key = broken.paths().key_file  # the same key, so the same identity keys and run
    key.parent.mkdir(mode=0o700)
    key.write_bytes(whole.paths().key_file.read_bytes())
    key.chmod(0o600)
    with pytest.raises(Crash):
        start(broken, four_units(), FailAt(2, "written"))
    start(broken, four_units())
    query = ("SELECT r.url, u.window_start, d.item_key, d.path, d.blob, d.introduced_at, "
             "d.c1_date, d.time_to_event_days, d.censoring_reason, d.censoring_date, "
             "d.clock_anomaly FROM debt_item d JOIN unit u USING (unit_id) "
             "JOIN repository r USING (repository_id) ORDER BY 1, 2, 3, 4, 6")
    assert rows(broken.store, query) == rows(whole.store, query)
    commits = "SELECT sha, author_name_key, message, trace_status FROM 'commit' ORDER BY sha"
    assert rows(broken.store, commits) == rows(whole.store, commits)


def test_the_duplicate_guard_refuses_to_store_a_unit_twice(
    built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a completed unit were ever processed again, its transaction fails; nothing changes."""
    workspace = built.workspace(tmp_path / "ws")
    text = run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS[:1], None)])
    with pytest.raises(Crash):
        start(workspace, text, FailAt(1, "completed"))  # unit stored, run still running
    before = dump(workspace.store)
    monkeypatch.setattr(ResearchJob, "_completed_keys", lambda self, run_id: set())
    with pytest.raises(RuntimeError, match="already stored"):
        start(workspace, text)
    assert dump(workspace.store) == before


def test_a_recorded_head_missing_from_a_fresh_clone_exits_3(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-49: the resumed repository's recorded HEAD is gone (history rewritten): exit 3."""
    workspace = built.workspace(tmp_path / "ws")
    with pytest.raises(Crash):
        start(workspace, four_units(), FailAt(1, "completed"))
    rewritten = make_fx_rg1.build(tmp_path / "rewritten")  # v1 only: v2's HEAD is absent
    workspace.access.sources["tdea-fixtures/fx-rg1"] = rewritten.path
    before = dump(workspace.store)
    capsys.readouterr()
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.startswith("Error: History changed\nCause: A fresh clone of "
                          f"{FX_RG1_URL} does not contain commit {built.fx_rg1.shas['c_fix']}, "
                          "the HEAD recorded when this run started")
    assert dump(workspace.store) == before
    assert not list(workspace.clones.iterdir())


def test_a_stored_history_that_differs_is_refused(built: Built, tmp_path: Path,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """A history read again that differs from the stored one is refused (exit 3)."""
    workspace = built.workspace(tmp_path / "ws")
    with pytest.raises(Crash):
        start(workspace, four_units(), FailAt(1, "completed"))
    rows(workspace.store, "UPDATE 'commit' SET first_parent_position = -first_parent_position "
                          "- 1 WHERE first_parent_position IN (0, 1)")
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_REFUSED
    assert capsys.readouterr().err.startswith(
        "Error: History changed\nCause: The first-parent history read again from")


def test_a_binary_diff_on_a_tracked_file_stops_with_an_undecided_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Day 6 reading: tracking cannot match through a binary diff; the unit is not stored."""
    repo = FixtureRepo(tmp_path / "binary")
    author = make_fx_rg1.ASHA
    repo.commit("Add module", {"pkg/tracked_b.py": 'def f():\n    return f"x"\n'},
                author=author, date="2026-05-01T09:00:00+00:00")
    repo.git("mv", "pkg/tracked_b.py", "pkg/tracked_c.py")
    repo.commit("Rename it to a path Git diffs as binary", {  # attributes apply from HEAD
        ".gitattributes": "pkg/tracked_c.py binary\n",
        "pkg/tracked_c.py": 'def f():\n    """Doc."""\n    return f"x"\n'},
        author=author, date="2026-05-02T09:00:00+00:00")
    workspace = Workspace(tmp_path / "ws", LocalAccess({"tdea-fixtures/fx-rg1": repo.path}))
    workspace.root.mkdir()
    text = run_file_text([(FX_RG1_URL, ("{start: 2026-05-01, end: 2026-05-02}",), None)])
    assert main(["research", "run", str(workspace.run_file(text))],
                service=workspace.service()) == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith("Error: Item tracking stopped\nCause: https://github.com/tdea-fixtures"
                          "/fx-rg1: Commit ")
    assert "binary" in err and cli.PLACEHOLDER_NOTE in err
    assert ("Exit code unresolved for this tracking stop on a binary diff: no source defines "
            "its exit code.") in err
    assert rows(workspace.store, "SELECT count(*) FROM unit") == [(0,)]
    assert not list(workspace.clones.iterdir())


def test_a_failed_accessibility_check_stops_with_an_undecided_code(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CL-19 is undecided: a failed FR-03 check is printed in three parts with the note."""
    workspace = built.workspace(tmp_path / "ws")
    workspace.access.failure = TdeaError(ErrorCode.NOT_FOUND, "Repository not found",
                                         "GitHub has no public repository.", "Check it.")
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith(f"Error: Repository not found\nCause: {FX_RG1_URL}: GitHub has no")
    assert cli.PLACEHOLDER_NOTE in err
    assert ("Exit code unresolved for this failed accessibility check at run time (CL-19 "
            "open)") in err
    assert rows(workspace.store, "SELECT status FROM run") == [("running",)]


def test_a_clone_failure_ends_with_exit_4_and_running_again_resumes(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-48 (Freeze rev. 116), RE-01 step 7: a unit stopped on a clone error exits 4.

    FX-PAIRS cannot be cloned, so its units stop after FX-RG1's two have committed; once
    the clone source is back, running the same run file again resumes: FX-RG1's units are
    kept unchanged and none of their files is linted again, and the run completes (exit 0).
    """
    workspace = built.workspace(tmp_path / "ws")
    workspace.access.sources["tdea-fixtures/fx-pairs"] = tmp_path / "no-such-repository"
    run_file = workspace.run_file(four_units())
    assert main(["research", "run", str(run_file)], service=workspace.service()) == 4
    err = capsys.readouterr().err
    assert err.startswith("Error: Clone failed\nCause: https://github.com/tdea-fixtures/fx-pairs")
    assert cli.INCOMPLETE_NOTE in err and cli.PLACEHOLDER_NOTE not in err
    assert "Exit code unresolved" not in err and not workspace.clone_entries()
    assert rows(workspace.store, "SELECT count(*), min(status) FROM unit") == [(2, "complete")]
    assert rows(workspace.store, "SELECT status FROM run") == [("running",)]
    before = unit_1_records(workspace.store)

    workspace.access.sources["tdea-fixtures/fx-pairs"] = built.fx_pairs
    profile = CountingProfile()
    assert main(["research", "run", str(run_file)],
                service=workspace.service(profile=profile)) == EXIT_OK
    assert unit_1_records(workspace.store) == before
    assert not unit_1_blobs(workspace.store) & set(profile.blobs)
    assert rows(workspace.store, "SELECT count(*), min(status) FROM unit") == [(4, "complete")]
    assert rows(workspace.store, "SELECT status FROM run") == [("complete",)]


class RefusingClassifier(SyntheticClassifier):
    """SYNTHETIC — not CL-03: claims complete lists but refuses to classify, as CL-03 v0.1 does."""

    def classify(self, raw):
        """Refuse as the unresolved matcher does."""
        raise MatchingRulesUnresolved(UnresolvedMatcher.reason)


def test_a_cl03_refusal_during_a_run_exits_3_and_stores_nothing_for_the_repository(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lists marked complete with matching still undefined: refused (exit 3), never guessed."""
    workspace = built.workspace(tmp_path / "ws")
    rules = Rg1Rules(RefusingClassifier(), SyntheticPackages(FX_RG1_PACKAGES),
                     SyntheticBulkRules())
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service(rules)) == EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.startswith("Error: Research run refused\nCause: CL-03 v0.1 does not define")
    assert rows(workspace.store, "SELECT count(*) FROM repository") == [(0,)]
    assert not list(workspace.clones.iterdir())


def test_the_real_access_checks_github_with_the_token_and_clones_the_https_address(
    canary_token: str
) -> None:
    """GitHubAccess: FR-03 through GitHubClient (token in the header only); FR-06 address."""
    requests = []

    class Response:
        """A 200 answer for a public, non-empty repository."""

        def __enter__(self):
            """Enter."""
            return self

        def __exit__(self, *_exc: object) -> None:
            """Exit."""

        def read(self) -> bytes:
            """Return the repository JSON."""
            return b'{"private": false, "size": 10, "default_branch": "main"}'

    def urlopen(request, timeout, context):
        """Record the request instead of sending it."""
        requests.append((request.full_url, request.get_header("Authorization"), timeout,
                         context is not None))
        return Response()

    access = GitHubAccess(GitHubClient(urlopen=urlopen))
    reference = RepositoryRef("owner", "name")
    access.check(reference, canary_token)
    assert requests == [("https://api.github.com/repos/owner/name",
                         f"Bearer {canary_token}", 10.0, True)]
    assert access.clone_source(reference).clone_url == "https://github.com/owner/name"


def test_only_package_files_are_linted_and_counted(tmp_path: Path) -> None:
    """RE-05 reading: a non-package file is never linted, so its lint failure is not counted.

    SYNTHETIC — not CL-03: which paths are package code is the test double's list.
    """
    repo = FixtureRepo(tmp_path / "failures")
    repo.commit("Add files", {"pkg/tracked_c.py": "def f(:\n",  # fails to lint
                              "tests/broken.py": "def g(:\n",  # fails, but not package
                              "pkg/tracked_d.py": 'def h():\n    return f"x"\n'},
                author=make_fx_rg1.ASHA, date="2026-05-01T09:00:00+00:00")
    workspace = Workspace(tmp_path / "ws", LocalAccess({"tdea-fixtures/fx-rg1": repo.path}))
    workspace.root.mkdir()
    profile = CountingProfile()
    text = run_file_text([(FX_RG1_URL, ("{start: 2026-05-01, end: 2026-05-02}",), None)])
    start(workspace, text, profile=profile)
    assert rows(workspace.store, "SELECT lint_failures, introductions FROM unit") == [(1, 1)]
    linted = rows(workspace.store, "SELECT new_blob, new_path, is_package FROM file_change "
                                   "ORDER BY new_path")
    assert [(path, bool(package)) for _blob, path, package in linted] == [
        ("pkg/tracked_c.py", True), ("pkg/tracked_d.py", True), ("tests/broken.py", False)]
    assert linted[2][0] not in profile.blobs and {linted[0][0], linted[1][0]} <= set(
        profile.blobs)


def test_only_leftover_clones_are_removed_from_the_clone_folder(tmp_path: Path) -> None:
    """FR-10: clones named by LocalRepository go, links are unlinked, other entries stay."""
    folder = tmp_path / "clones"
    stale = folder / "tdea-clone-abc123"
    (stale / ".git" / "objects").mkdir(parents=True)
    locked = stale / ".git" / "objects" / "pack"
    locked.write_bytes(b"pack")
    locked.chmod(0o444)  # git marks objects read-only
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    try:
        (folder / "tdea-clone-link").symlink_to(outside, target_is_directory=True)
        links = 1
    except OSError:  # Windows without the symbolic-link privilege
        links = 0
    (folder / "notes.txt").write_bytes(b"not a clone")
    assert remove_stale_clones(folder) == 1 + links
    assert sorted(entry.name for entry in folder.iterdir()) == ["notes.txt"]
    assert (outside / "keep.txt").read_bytes() == b"keep"
    assert remove_stale_clones(tmp_path / "new" / "clones") == 0
    assert (tmp_path / "new" / "clones").is_dir()


def test_a_clone_folder_that_is_a_link_is_refused_not_followed(tmp_path: Path) -> None:
    """The clean-up never goes through a link: a linked clone folder is refused, untouched."""
    target = tmp_path / "elsewhere"
    victim = target / "tdea-clone-victim"
    victim.mkdir(parents=True)
    link = tmp_path / "clones"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not permitted here")
    with pytest.raises(NotADirectoryError):
        remove_stale_clones(link)
    assert victim.is_dir()


def test_day7_records_are_written_with_their_units(built: Built, tmp_path: Path) -> None:
    """FR-56 records in each unit's transaction: a base rate for every item, a disappearance
    and four modes for every item with c1, and four summaries for every unit, also for a
    unit without disappearances, whose share is null (not 0) (Day 7, P1)."""
    workspace = built.workspace(tmp_path / "ws")
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_OK
    store = workspace.store
    (items,) = rows(store, "SELECT count(*) FROM debt_item")[0]
    (with_c1,) = rows(store, "SELECT count(*) FROM debt_item WHERE c1_commit_id IS NOT NULL")[0]
    assert (items, with_c1) == (15, 4)
    assert rows(store, "SELECT count(*) FROM base_rate") == [(items,)]
    assert rows(store, "SELECT count(*) FROM disappearance") == [(with_c1,)]
    assert rows(store, "SELECT count(*) FROM item_mode") == [(4 * with_c1,)]
    assert rows(store, "SELECT count(*), count(DISTINCT unit_id) FROM unit_summary") == [(16, 4)]
    assert rows(store, "SELECT tau, disappearances, local_fixes, local_fix_share FROM "
                       "unit_summary JOIN unit USING (unit_id) WHERE window_start LIKE "
                       "'2026-01-01%' ORDER BY tau") == [
        (0.5, 0, 0, None), (0.6, 0, 0, None), (0.7, 0, 0, None), (0.8, 0, 0, None)]
    assert rows(store, "SELECT count(*) FROM item_mode WHERE unvalidated = 1") == [
        (4 * with_c1,)]


# --- Task 7 Day 8: covariate rows, their transaction and the stored-run check -----------

LEGACY_NOTE = ("Exit code unresolved for this research store missing required records: no "
               "source defines its exit code.")


def files(workspace: Workspace) -> dict[str, str]:
    """Return every folder and file under the workspace (run file, research.db and any
    journal, key file, lint cache, lock folder and lock file, clone folder and clones),
    files by SHA-256. Nothing is opened as a database, so a hot journal stays hot."""
    found = {}
    for entry in sorted(workspace.root.rglob("*")):
        name = entry.relative_to(workspace.root).as_posix()
        found[name] = ("link" if entry.is_symlink() else "folder" if entry.is_dir()
                       else hashlib.sha256(entry.read_bytes()).hexdigest())
    return found


def state(workspace: Workspace) -> tuple[dict[str, str], dict[str, list[tuple]]]:
    """Return everything the run can leave behind: files() and every row of research.db."""
    return files(workspace), dump(workspace.store)


def alter(store: Path, statement: str) -> None:
    """Change research.db as an earlier TDEA build would have left it (test set-up only)."""
    with sqlite3.connect(store) as connection:
        connection.execute(statement)
    connection.close()


def test_day8_covariate_rows_are_written_with_their_units(built: Built, tmp_path: Path) -> None:
    """FR-57 records in each unit's transaction: one covariate row per debt item, author_id
    the introducing commit's author e-mail key, the month t's, tenure null (deferred, Q9)."""
    workspace = built.workspace(tmp_path / "ws")
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == EXIT_OK
    store = workspace.store
    assert rows(store, "SELECT count(*), count(DISTINCT item_id) FROM covariate") == \
        rows(store, "SELECT count(*), count(DISTINCT item_id) FROM debt_item") == [(15, 15)]
    assert rows(store, "SELECT count(*) FROM covariate v JOIN debt_item d USING (item_id) "
                       "JOIN 'commit' c ON c.commit_id = d.introducing_commit_id "
                       "WHERE v.author_id IS c.author_email_key "
                       "AND v.month = substr(d.introduced_at, 1, 7) "
                       "AND v.account_tenure_days IS NULL AND v.author_id IS NOT NULL") == [(15,)]


def test_a_failure_while_storing_covariates_rolls_the_whole_unit_back(
    built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR-08 for FR-57: a covariate row the store rejects (simulated: an author_id that is
    not a key, which the frozen schema refuses) rolls back unit 3 whole, FX-PAIRS's history
    included; units 1 and 2 keep theirs; the resumed run then stores every row once."""
    workspace = built.workspace(tmp_path / "ws")
    original = unit_run.CovariateExtractor.extract

    def broken(self, introduction):
        found = original(self, introduction)
        if introduction.path.startswith("pairs/"):
            return dataclasses.replace(found, author_id="not-a-key")
        return found

    monkeypatch.setattr(unit_run.CovariateExtractor, "extract", broken)
    with pytest.raises(IntegrityError):
        start(workspace, four_units())
    store = workspace.store
    assert rows(store, "SELECT count(*) FROM unit") == [(2,)]
    assert rows(store, "SELECT url FROM repository") == [(FX_RG1_URL,)]
    assert rows(store, "SELECT count(*) FROM debt_item WHERE path LIKE 'pairs/%'") == [(0,)]
    for table in ("covariate", "base_rate"):
        assert rows(store, f"SELECT count(*) FROM {table}") == \
            rows(store, "SELECT count(*) FROM debt_item"), table
    assert rows(store, "SELECT count(*) FROM unit_summary") == [(8,)]
    assert not list(workspace.clones.iterdir())
    monkeypatch.setattr(unit_run.CovariateExtractor, "extract", original)
    start(workspace, four_units())
    assert rows(store, "SELECT count(*), count(DISTINCT item_id) FROM covariate") == [(15, 15)]
    assert rows(store, "SELECT count(*), min(status) FROM unit") == [(4, "complete")]


def run_cli(workspace: Workspace, run_file: Path) -> int:
    """Run ``run_file`` through the command line."""
    return main(["research", "run", str(run_file)], service=workspace.service())


def assert_refused(code: int, err: str) -> None:
    """The run was refused through the unresolved-code path (decision R4.3)."""
    assert code == cli.EXIT_UNDECIDED
    assert err.startswith("Error: Research store incomplete\nCause: Run ")
    assert LEGACY_NOTE in err and cli.PLACEHOLDER_NOTE in err


def legacy_workspace(built: Built, root: Path, kind: str, statement: str) -> Workspace:
    """Return a workspace whose run is complete or resumable, then made invalid."""
    workspace = built.workspace(root)
    if kind == "complete":
        assert run_cli(workspace, workspace.run_file(four_units())) == EXIT_OK
    else:
        with pytest.raises(Crash):
            start(workspace, four_units(), FailAt(2, "completed"))
    alter(workspace.store, statement)
    return workspace


ABSENT = {
    "no-lint-cache": lambda ws: shutil.rmtree(ws.paths().lint_cache.parent),
    "no-lock-folder": lambda ws: shutil.rmtree(ws.paths().lock_directory),
    "stale-clone": lambda ws: (ws.clones / "tdea-clone-stale").mkdir(parents=True),
    "nothing-else": lambda ws: (shutil.rmtree(ws.paths().lint_cache.parent),
                                shutil.rmtree(ws.paths().lock_directory)),
}


@pytest.mark.parametrize("absent", sorted(ABSENT))
@pytest.mark.parametrize("kind", ["complete", "resumable"])
def test_a_refused_store_leaves_no_trace_whatever_state_is_absent(
    kind: str, absent: str, built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R3.3, decision A, strict: a complete or resumable run whose completed units lack
    covariate rows (as a store from Day 7 does) is refused before the lock, the key, the
    lint cache or the clone folder is touched. With the lint cache missing, the lock folder
    missing, a leftover clone present or both folders missing, every file and folder under
    the workspace, and every row, is exactly as before; no run identifier is printed."""
    workspace = legacy_workspace(built, tmp_path / "ws", kind, "DELETE FROM covariate")
    ABSENT[absent](workspace)
    run_file = workspace.run_file(four_units())
    before = state(workspace)
    capsys.readouterr()
    code = run_cli(workspace, run_file)
    captured = capsys.readouterr()
    assert_refused(code, captured.err)
    assert captured.out == ""
    assert state(workspace) == before
    assert rows(workspace.store, "SELECT status FROM run") == [
        ("complete" if kind == "complete" else "running",)]


@pytest.mark.parametrize("statement", [
    "DELETE FROM covariate WHERE item_id = (SELECT min(item_id) FROM covariate)",
    "DELETE FROM unit_summary WHERE unit_id = (SELECT min(unit_id) FROM unit) AND tau = 0.5",
    "INSERT INTO unit_summary SELECT unit_id, 0.9, 0, 0, NULL FROM unit "
    "WHERE unit_id = (SELECT min(unit_id) FROM unit)",
], ids=["missing-covariate", "missing-summary", "fifth-summary"])
def test_a_completed_run_with_missing_records_is_refused_not_reported_complete(
    statement: str, built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision A: a complete run is checked before "already complete"; one missing
    covariate row, a missing summary or a fifth one refuses it and changes nothing."""
    workspace = legacy_workspace(built, tmp_path / "ws", "complete", statement)
    run_file = workspace.run_file(four_units())
    before = state(workspace)
    capsys.readouterr()
    code = run_cli(workspace, run_file)
    captured = capsys.readouterr()
    assert_refused(code, captured.err)
    assert "1 completed unit(s)" in captured.err and captured.out == ""
    assert state(workspace) == before


@pytest.mark.parametrize("cause", ["store-busy", "changed-after-the-read"])
@pytest.mark.parametrize("kind", ["complete", "resumable"])
def test_a_store_the_read_only_check_cannot_settle_is_refused_under_the_lock(
    kind: str, cause: str, built: Built, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """Simulated: the read before the lock cannot refuse (the store is busy, or another
    process wrote the missing records just after it read). The check under the lock still
    refuses, before the clone folder or the lint cache is touched. Its only trace is the
    lock itself, which the concurrency guarantee needs: the lock folder and analysis.lock,
    created when missing and never deleted (lock.py). research.db, the key file and every
    row stay unchanged; no lint cache and no clone folder appear."""
    workspace = legacy_workspace(built, tmp_path / "ws", kind, "DELETE FROM covariate")
    ABSENT["nothing-else"](workspace)
    if cause == "store-busy":
        def busy(path):
            raise OperationalError("SELECT", {}, Exception("database is locked"))
        monkeypatch.setattr(ResearchStore, "read_only", staticmethod(busy))
    else:
        monkeypatch.setattr(ResearchService, "_refuse_incomplete_store",
                            lambda self, path, run_file: None)
    run_file = workspace.run_file(four_units())
    before_files, before_rows = state(workspace)
    capsys.readouterr()
    code = run_cli(workspace, run_file)
    captured = capsys.readouterr()
    assert_refused(code, captured.err)
    after_files, after_rows = state(workspace)
    assert after_rows == before_rows
    assert {name: after_files[name] for name in after_files if name not in before_files} == {
        "lock": "folder", "lock/analysis.lock": hashlib.sha256(b"").hexdigest()}
    assert {name: after_files[name] for name in before_files} == before_files


def test_valid_current_stores_pass_the_check(built: Built, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    """The check refuses only missing records: a resumed current-format run completes, and
    starting a complete one again reports it complete and writes nothing to research.db,
    also with completed units that have no debt item."""
    workspace = built.workspace(tmp_path / "ws")
    with pytest.raises(Crash):
        start(workspace, four_units(), FailAt(2, "completed"))
    run_file = workspace.run_file(four_units())
    assert run_cli(workspace, run_file) == EXIT_OK
    assert rows(workspace.store, "SELECT count(*) FROM unit u WHERE NOT EXISTS (SELECT 1 FROM "
                                 "debt_item d WHERE d.unit_id = u.unit_id)")[0][0] >= 1
    before = state(workspace)
    capsys.readouterr()
    assert run_cli(workspace, run_file) == EXIT_OK
    assert "is already complete; nothing to do." in capsys.readouterr().out
    assert state(workspace) == before


@pytest.mark.parametrize("kind", ["complete", "resumable"])
def test_valid_current_stores_resume_and_complete_with_state_absent(
    kind: str, built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the lint cache and the lock folder missing, a valid resumable run resumes and
    completes with one covariate row per item, and a valid complete run is reported
    complete: the read-only check refuses neither."""
    workspace = built.workspace(tmp_path / "ws")
    run_file = workspace.run_file(four_units())
    if kind == "complete":
        assert run_cli(workspace, run_file) == EXIT_OK
    else:
        with pytest.raises(Crash):
            start(workspace, four_units(), FailAt(2, "completed"))
    ABSENT["nothing-else"](workspace)
    capsys.readouterr()
    assert run_cli(workspace, run_file) == EXIT_OK
    out = capsys.readouterr().out
    if kind == "complete":
        assert "is already complete; nothing to do." in out
    else:
        assert ": 2 of 4 units already complete." in out
    assert rows(workspace.store, "SELECT count(*), min(status) FROM unit") == [(4, "complete")]
    assert rows(workspace.store, "SELECT count(*), count(DISTINCT item_id) FROM covariate") == [
        (15, 15)]
    assert rows(workspace.store, "SELECT status FROM run") == [("complete",)]


def test_the_check_before_the_lock_never_creates_a_key_file(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With research.db present and the key file missing, the read before the lock has no
    run to check (a new key would give a new run identifier) and writes nothing: when the
    lock is then found held, the refusal (exit 3, as before Day 8) leaves no key file."""
    workspace = built.workspace(tmp_path / "ws")
    run_file = workspace.run_file(four_units())
    assert run_cli(workspace, run_file) == EXIT_OK
    shutil.rmtree(workspace.paths().key_file.parent)
    before = state(workspace)
    lock = AnalysisLock(workspace.paths().lock_directory)
    lock.acquire()
    try:
        capsys.readouterr()
        code = run_cli(workspace, run_file)
    finally:
        lock.release()
    assert code == EXIT_REFUSED
    assert capsys.readouterr().err.startswith("Error: Another analysis is running\n")
    assert state(workspace) == before


# Test set-up only, in plain sqlite3: leave a genuine hot journal on research.db. A tiny page
# cache makes SQLite write changed pages into the database file before the commit, after
# saving the originals in research.db-journal; the process then dies without committing or
# rolling back, as a killed process would.
CRASH = """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1], isolation_level=None)
connection.execute('PRAGMA cache_size=10')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE file_change SET hunks = hunks || ?', (' ' * 3000,))
os._exit(0)
"""


@pytest.mark.parametrize("kind", ["complete", "resumable"])
def test_a_store_with_a_hot_journal_is_recovered_by_sqlite_then_refused_under_the_lock(
    kind: str, built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Residual trace 2, with a genuine hot journal (no simulation), on a disposable copy
    of an invalid store: the read before the lock cannot read a store that needs recovery,
    so the check under the lock decides. Opening research.db there lets SQLite roll the
    journal back; the store is then refused (exit 1) before the clone folder, the lint
    cache or the job is touched. The committed rows after recovery equal those before the
    crash, research.db's bytes equal the committed file's again, the journal is gone, and
    the only other new entries are the approved lock artifact (trace 1)."""
    legacy = legacy_workspace(built, tmp_path / "legacy", kind, "DELETE FROM covariate")
    workspace = built.workspace(tmp_path / "copy")  # the disposable copy
    shutil.copyfile(legacy.store, workspace.store)
    key = workspace.paths().key_file
    key.parent.mkdir(mode=0o700)
    shutil.copyfile(legacy.paths().key_file, key)
    key.chmod(0o600)
    shutil.rmtree(workspace.paths().lint_cache.parent)  # the copy has no lock folder either
    run_file = workspace.run_file(four_units())
    committed_rows = dump(workspace.store)
    committed_bytes = hashlib.sha256(workspace.store.read_bytes()).hexdigest()
    subprocess.run([sys.executable, "-c", CRASH, str(workspace.store)], check=True)
    journal = Path(f"{workspace.store}-journal")
    assert journal.is_file() and journal.stat().st_size > 0
    before = files(workspace)  # research.db now holds uncommitted pages
    assert before["research.db"] != committed_bytes
    with pytest.raises(ValueError):  # what the read before the lock meets: it cannot read
        ResearchStore.read_only(workspace.store)
    assert files(workspace) == before  # and that attempt changed nothing
    capsys.readouterr()
    code = run_cli(workspace, run_file)
    captured = capsys.readouterr()
    assert_refused(code, captured.err)
    assert captured.out.startswith("Run identifier: ")  # decided under the lock
    after = files(workspace)
    assert not journal.exists()
    assert after["research.db"] == committed_bytes
    assert dump(workspace.store) == committed_rows
    changed = {name for name in before.keys() & after.keys() if before[name] != after[name]}
    assert changed == {"research.db"}
    assert set(before) - set(after) == {"research.db-journal"}
    assert {name: after[name] for name in set(after) - set(before)} == {
        "lock": "folder", "lock/analysis.lock": hashlib.sha256(b"").hexdigest()}
