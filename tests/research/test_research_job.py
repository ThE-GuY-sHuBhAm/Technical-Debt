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
* Failures without an agreed exit code (a binary diff on a tracked file, a failed
  accessibility check, a clone failure) stop the run, keep earlier units and are printed
  in three parts with the undecided note (decision 3); SRS v2.3's exit 4 stays BLOCKED.
"""

from __future__ import annotations

import os
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

from tdea.aggregator.research_job import ResearchJob, RunObserver, plan_units
from tdea.aggregator.research_service import ResearchRunError
from tdea.analyzer.research.engine import GitHubAccess, Rg1Rules
from tdea.analyzer.research.profile import ResearchProfile
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


def test_a_clone_failure_names_cl15_and_does_not_claim_a_code(
    built: Built, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CL-15 open: a clone failure is reported as such; status 1 is only a placeholder."""
    workspace = built.workspace(tmp_path / "ws")
    workspace.access.sources["tdea-fixtures/fx-rg1"] = tmp_path / "no-such-repository"
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith("Error: Clone failed\nCause: https://github.com/tdea-fixtures/fx-rg1")
    assert "Exit code unresolved for this clone failure (CL-15 open)" in err
    assert cli.PLACEHOLDER_NOTE in err and not workspace.clone_entries()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: SRS v2.3 gives exit 4 for a unit stopped on a clone, "
                          "network or disk error; the Freeze has no such code (Day 6 "
                          "decision 3: undecided)")
def test_srs_exit_4_for_a_clone_failure(built: Built, tmp_path: Path) -> None:
    """SRS v2.3 4.5.1: a clone failure ends with exit 4. Not implemented while undecided."""
    workspace = built.workspace(tmp_path / "ws")
    workspace.access.sources["tdea-fixtures/fx-rg1"] = tmp_path / "no-such-repository"
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == 4


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
