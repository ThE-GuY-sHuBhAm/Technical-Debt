"""RE-01 refusals and the run/status command line, before any clone or lint (FR-48, Day 6).

These checks need neither Pylint nor a clone, so they run in every CI cell:

* CL-03 refusal (Task 7 Day 6 decision 1): with the real rule lists, ``run`` exits 3
  before it takes the lock, contacts GitHub, clones, or creates the key file, research.db
  or the lint cache. This is the behaviour every real run has today.
* NFR-24: an environment other than the pinned one exits 3 first.
* CON-03, OI-04 (RE-01 step 5, mechanics): a held lock exits 3 at once and nothing is
  written. Reaching the lock needs the CL-03 check passed, so this uses the SYNTHETIC —
  not CL-03 rules of run_support; it is mechanics, not RE-01 evidence.
* The PAT comes from TDEA_GITHUB_TOKEN or --ask-token, reaches the service and is never
  printed (FR-04, NFR-19); undecided exit codes are reported as such (decision 3).
* An RG10 bundle schema error (BundleInvalid, FR-58) takes the agreed error path: three
  parts and exit 1 (Day 9 decision D10). RG10 runs are not built yet, so the error is
  raised from the rule-list check in its place; this checks the mapping only.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from run_files import schema_example
from run_support import FX_RG1_URL, PINNED_FACTS, LocalAccess, Workspace, four_units

from tdea.aggregator import key_store, lint_cache, lock, research_store
from tdea.aggregator.lock import AnalysisLock
from tdea.aggregator.research_job import RunContext
from tdea.aggregator.research_service import ResearchRunError, ResearchService
from tdea.aggregator.run_file import Problem, RunFileInvalid
from tdea.analyzer.research import engine
from tdea.analyzer.research.engine import ResearchEngine
from tdea.analyzer.research.profile import EnvironmentFacts
from tdea.analyzer.research.rg10.bundle import BundleInvalid
from tdea.api import cli
from tdea.api.cli import EXIT_INVALID_INPUT, EXIT_OK, EXIT_REFUSED, main
from tdea.git_handler import github_client, local_repository
from tdea.git_handler.errors import ErrorCode, TdeaError

POSIX = os.name != "nt"


@pytest.fixture(name="workspace")
def _workspace(tmp_path: Path) -> Workspace:
    """Return an empty workspace whose access double knows no repository."""
    return Workspace(tmp_path, LocalAccess({}))


def tripwires(monkeypatch: pytest.MonkeyPatch, *, lock_too: bool = True) -> None:
    """Make every clone, GitHub request and file creation of a run fail the test."""

    def tripwire(*_args: object, **_kwargs: object) -> None:
        """Fail: the refusal must come first."""
        raise AssertionError("a refused run attempted a side effect")

    targets = [(local_repository.LocalRepository, "clone"),
               (github_client.GitHubClient, "check_repository"),
               (key_store.KeyStore, "__init__"), (research_store.ResearchStore, "__init__"),
               (lint_cache.LintCache, "__init__")]
    if lock_too:
        targets.append((lock.AnalysisLock, "acquire"))
    for owner, name in targets:
        monkeypatch.setattr(owner, name, tripwire)


def files_in(root: Path) -> list[str]:
    """Return every path under ``root``, relative."""
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_cl03_refuses_every_real_run_before_lock_clone_or_write(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision 1: the real rule lists are incomplete, so run exits 3 and touches nothing."""
    tripwires(monkeypatch)
    run_file = workspace.run_file(schema_example())
    service = ResearchService(ResearchEngine(), paths=workspace.paths(),
                              environment=PINNED_FACTS)
    assert main(["research", "run", str(run_file)], service=service) == EXIT_REFUSED == 3
    output = capsys.readouterr()
    assert output.out == ""
    error, cause, action = output.err.splitlines()
    assert error == "Error: Research run refused"
    assert "CL-03" in cause and "incomplete" in cause and "FR-54" in cause
    assert "bulk_config_files and bulk_message_patterns" in cause and "FR-56 a1, a3" in cause
    assert action.startswith("Action: Run research only")
    assert files_in(workspace.root) == ["run.yaml"]


def test_a_bundle_schema_error_exits_1_through_the_agreed_path(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """D10: BundleInvalid reaches the command line as three parts and exit 1, writing nothing."""
    tripwires(monkeypatch)
    error = BundleInvalid("variants/T1/clean/shop/orders.py", None, None,
                          "does not parse under Python 3.11 (SyntaxError: invalid syntax "
                          "(line 31)), so new imports and redefinitions cannot be determined")

    def refuse(_engine: ResearchEngine) -> None:
        """Raise the bundle error where a real RG10 run would meet it."""
        raise error

    monkeypatch.setattr(ResearchEngine, "check_rules", refuse)
    run_file = workspace.run_file(schema_example())
    service = ResearchService(ResearchEngine(), paths=workspace.paths(),
                              environment=PINNED_FACTS)
    assert main(["research", "run", str(run_file)], service=service) == EXIT_INVALID_INPUT == 1
    err_type, cause, action = capsys.readouterr().err.splitlines()
    assert err_type == "Error: RG10 bundle invalid"
    assert cause == f"Cause: {error.cause}"
    assert action == f"Action: {BundleInvalid.action}"
    assert files_in(workspace.root) == ["run.yaml"]


def test_an_unpinned_environment_is_refused_first(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NFR-24 comes before the CL-03 check: Python 3.12 exits 3, naming the expected version."""
    tripwires(monkeypatch)
    run_file = workspace.run_file(schema_example())
    facts = EnvironmentFacts((3, 12, 4), True, "4.1.1", "4.3.3", "6.0.1")
    service = ResearchService(ResearchEngine(), paths=workspace.paths(), environment=facts)
    assert main(["research", "run", str(run_file)], service=service) == EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.startswith("Error: Research environment refused\nCause: Python 3.12.4")
    assert "CL-03" not in err and files_in(workspace.root) == ["run.yaml"]


def test_an_invalid_run_file_exits_1_before_anything(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    canary_token: str
) -> None:
    """RE-01 step 3 through run: a token in the file exits 1 and is never echoed."""
    tripwires(monkeypatch)
    run_file = workspace.run_file(schema_example().replace("seeds:", f"# {canary_token}\nseeds:"))
    assert main(["research", "run", str(run_file)], service=workspace.service()) \
        == EXIT_INVALID_INPUT
    output = capsys.readouterr()
    assert output.err.startswith("Error: Run file contains a token")
    assert canary_token not in output.out + output.err
    assert files_in(workspace.root) == ["run.yaml"]


def test_synthetic_a_held_lock_exits_3_and_nothing_is_written(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SYNTHETIC — not CL-03: RE-01 step 5 mechanics; the second run is rejected at once."""
    tripwires(monkeypatch, lock_too=False)
    run_file = workspace.run_file(four_units())
    with AnalysisLock(workspace.paths().lock_directory):
        code = main(["research", "run", str(run_file)], service=workspace.service())
    assert code == EXIT_REFUSED
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("Error: Another analysis is running\nCause: TDEA runs one")
    assert workspace.access.tokens == []
    assert files_in(workspace.root) == ["lock", "lock/analysis.lock", "run.yaml"]


def test_synthetic_a_refused_second_run_leaves_the_first_runs_clones_alone(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SYNTHETIC — not CL-03: stale clones are cleared only by the lock holder (FR-10)."""
    tripwires(monkeypatch, lock_too=False)
    in_use = workspace.clones / "tdea-clone-in-use"
    with AnalysisLock(workspace.paths().lock_directory):  # the first run, cloning
        in_use.mkdir(parents=True)
        assert main(["research", "run", str(workspace.run_file(four_units()))],
                    service=workspace.service()) == EXIT_REFUSED
    assert in_use.is_dir()


@pytest.mark.parametrize("target, what", [("store", "research store"),
                                          ("cache", "lint cache")])
@pytest.mark.parametrize("content", ["not sqlite", "other tables"])
def test_synthetic_a_store_or_cache_of_another_kind_is_refused_in_three_parts(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    target: str, what: str, content: str
) -> None:
    """SYNTHETIC — not CL-03: no traceback and no write; the code stays undecided."""
    monkeypatch.setattr(local_repository.LocalRepository, "clone", tripwire_clone)
    path = workspace.store if target == "store" else workspace.paths().lint_cache
    path.parent.mkdir(parents=True, exist_ok=True)
    if content == "not sqlite":
        path.write_bytes(b"not a database" * 100)
    else:
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE repositories (id INTEGER)")
    before = path.read_bytes()
    assert main(["research", "run", str(workspace.run_file(four_units()))],
                service=workspace.service()) == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith(f"Error: Not a {what}\nCause: {path} cannot be used as the {what}")
    assert "Traceback" not in err and cli.PLACEHOLDER_NOTE in err
    assert ("Exit code unresolved for this store or lint cache of another kind: no source "
            "defines its exit code.") in err
    assert path.read_bytes() == before and workspace.access.tokens == []


def tripwire_clone(*_args: object, **_kwargs: object) -> None:
    """Fail: nothing may be cloned when the store or cache cannot be used."""
    raise AssertionError("cloned although the store or cache was unusable")


@pytest.mark.skipif(not POSIX, reason="owner-only permissions are a POSIX check")
def test_synthetic_an_unusable_lock_folder_exits_3(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SYNTHETIC — not CL-03: a lock folder others can write is refused with exit 3."""
    tripwires(monkeypatch, lock_too=False)
    folder = workspace.paths().lock_directory
    folder.mkdir(mode=0o777)
    folder.chmod(0o777)
    run_file = workspace.run_file(four_units())
    assert main(["research", "run", str(run_file)], service=workspace.service()) \
        == EXIT_REFUSED
    assert capsys.readouterr().err.startswith("Error: Analysis lock unavailable")


def test_status_without_a_store_creates_nothing(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """status is read-only: no store, lock, key file or lint cache is created."""
    tripwires(monkeypatch)
    run_file = workspace.run_file(schema_example())
    assert main(["research", "status", str(run_file)], service=workspace.service()) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out == [f"Research store: {workspace.store.resolve()}",
                   "No research store exists at this path yet, so there are no runs."]
    assert files_in(workspace.root) == ["run.yaml"]


def test_status_checks_the_run_file(workspace: Workspace,
                                    capsys: pytest.CaptureFixture[str]) -> None:
    """status validates its run file first (exit 1)."""
    run_file = workspace.run_file(schema_example().replace("w_months: 6\n", ""))
    assert main(["research", "status", str(run_file)], service=workspace.service()) \
        == EXIT_INVALID_INPUT
    assert "W is missing" in capsys.readouterr().err


def test_status_refuses_a_file_that_is_not_a_research_store(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """A store path naming another file is reported in three parts, never opened for writing."""
    workspace.store.write_bytes(b"not a database" * 100)
    run_file = workspace.run_file(schema_example())
    assert main(["research", "status", str(run_file)], service=workspace.service()) \
        == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith("Error: Not a research store") and cli.PLACEHOLDER_NOTE in err
    assert "Exit code unresolved for this store or lint cache of another kind:" in err
    assert workspace.store.read_bytes() == b"not a database" * 100


class RecordingService:
    """Stands in for ResearchService: records the token it is given, or raises."""

    def __init__(self, error: Exception | None = None) -> None:
        """Raise ``error`` from run(), if given."""
        self.error = error
        self.tokens: list[str | None] = []

    def run(self, path: str, token: str | None = None, observer: object = None) -> None:
        """Record the call."""
        assert path and observer is not None
        self.tokens.append(token)
        if self.error is not None:
            raise self.error


def test_the_pat_comes_from_the_environment_and_is_never_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], canary_token: str
) -> None:
    """FR-04: TDEA_GITHUB_TOKEN reaches the service; an empty variable means no token."""
    service = RecordingService()
    monkeypatch.setenv("TDEA_GITHUB_TOKEN", canary_token)
    assert main(["research", "run", "run.yaml"], service=service) == EXIT_OK
    monkeypatch.setenv("TDEA_GITHUB_TOKEN", "")
    assert main(["research", "run", "run.yaml"], service=service) == EXIT_OK
    monkeypatch.delenv("TDEA_GITHUB_TOKEN")
    assert main(["research", "run", "run.yaml"], service=service) == EXIT_OK
    assert service.tokens == [canary_token, None, None]
    output = capsys.readouterr()
    assert canary_token not in output.out + output.err


def test_ask_token_prompts_without_echo(monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str],
                                        canary_token: str) -> None:
    """--ask-token reads the PAT through getpass, which does not echo; it wins over the variable."""
    prompts: list[str] = []
    monkeypatch.setattr(cli.getpass, "getpass",
                        lambda prompt: prompts.append(prompt) or canary_token)
    monkeypatch.setenv("TDEA_GITHUB_TOKEN", "ghp_" + "x" * 36)
    service = RecordingService()
    assert main(["research", "run", "run.yaml", "--ask-token"], service=service) == EXIT_OK
    assert service.tokens == [canary_token] and len(prompts) == 1
    assert canary_token not in capsys.readouterr().out


@pytest.mark.parametrize("error, code", [
    (ResearchRunError("History changed", "c", "a", 3), 3),
    (ResearchRunError("Invalid commit list", "c", "a", 1), 1),
    (ResearchRunError("Clone failed", "c", "a", 4, "clone failure"), 4),
    (ResearchRunError("Repository not found", "c", "a", None, "failed accessibility check",
                      "CL-19"), cli.EXIT_UNDECIDED),
])
def test_exit_codes_follow_the_error(error: ResearchRunError, code: int,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    """Agreed codes pass through; an undecided one prints the note and exits 1 meanwhile."""
    assert main(["research", "run", "run.yaml"], service=RecordingService(error)) == code
    err = capsys.readouterr().err
    assert err.startswith(f"Error: {error.error_type}\nCause: c\nAction: a")
    assert ("Exit code unresolved" in err) == (cli.PLACEHOLDER_NOTE in err) \
        == (error.exit_code is None)
    assert (cli.INCOMPLETE_NOTE in err) == (code == cli.EXIT_INCOMPLETE == 4)


def test_an_invalid_run_file_from_run_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    """RunFileInvalid raised by run is printed problem by problem and exits 1."""
    invalid = RunFileInvalid([Problem("Invalid run file", "c1", "a1"),
                              Problem("Invalid run file", "c2", "a2")])
    assert main(["research", "run", "run.yaml"], service=RecordingService(invalid)) \
        == EXIT_INVALID_INPUT
    assert capsys.readouterr().err.split("\n\n")[1].startswith("Error: Invalid run file\nCause: c2")


def test_synthetic_an_unusable_key_file_is_reported_with_an_undecided_code(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """SYNTHETIC — not CL-03: a key file of the wrong length is refused and never replaced."""
    key = workspace.paths().key_file
    key.parent.mkdir(mode=0o700)
    key.write_bytes(b"short")
    key.chmod(0o600)
    run_file = workspace.run_file(four_units())
    assert main(["research", "run", str(run_file)], service=workspace.service()) \
        == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith("Error: Key file unusable\nCause: The key file ")
    assert key.read_bytes() == b"short" and cli.PLACEHOLDER_NOTE in err
    assert ("Exit code unresolved for this unusable key file: no source defines its exit "
            "code.") in err
    assert not workspace.store.exists() and workspace.access.tokens == []


def test_synthetic_a_store_that_cannot_be_written_stops_the_run(
    workspace: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    """SYNTHETIC — not CL-03: a disk or file failure is reported in three parts, exit 4 (FR-48)."""
    workspace.store.mkdir()  # research.db is a folder: SQLite cannot open it
    run_file = workspace.run_file(four_units())
    assert main(["research", "run", str(run_file)], service=workspace.service()) == 4
    err = capsys.readouterr().err
    assert err.startswith("Error: Run stopped\nCause: A file or database operation failed")
    assert "sqlite" not in err.lower() and cli.PLACEHOLDER_NOTE not in err
    assert cli.INCOMPLETE_NOTE in err and "Exit code unresolved" not in err
    assert workspace.access.tokens == []


def test_status_of_a_store_without_runs(workspace: Workspace,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    """A research store with no run is listed as such."""
    research_store.ResearchStore(workspace.store).close()
    run_file = workspace.run_file(schema_example())
    assert main(["research", "status", str(run_file)], service=workspace.service()) == EXIT_OK
    assert capsys.readouterr().out.splitlines()[1] == "No runs in this store."


def test_the_run_context_never_shows_its_key_or_token(canary_token: str) -> None:
    """NFR-19, NFR-27: a RunContext's repr names the run only."""
    context = RunContext("a" * 12, None, "{}", "0.1.0", bytes(range(32)), "f" * 16, {},
                         token=canary_token)  # type: ignore[arg-type]
    shown = repr(context)
    assert canary_token not in shown and "key and token withheld" in shown
    assert bytes(range(32)).hex() not in shown


@pytest.mark.parametrize("category, open_item, expected", [
    ("failed accessibility check", "CL-19", "Exit code unresolved for this failed "
     "accessibility check at run time (CL-19 open): no source defines its exit code."),
    ("history read failure", None, "Exit code unresolved for this history read failure: no "
     "source defines its exit code."),
    (None, None, "Exit code unresolved for this failure: no source defines its exit code."),
])
def test_unresolved_notes_name_the_category_and_the_open_item(
    category: str | None, open_item: str | None, expected: str
) -> None:
    """Decision 3: each note names the failure category and why its code is unresolved."""
    assert cli.unresolved_note(category, open_item) == expected
    assert "invalid" not in cli.unresolved_note(category, open_item).lower()


def test_the_open_items_the_command_line_names_are_the_engines() -> None:
    """The command line's CL-19 note matches the engine's open-item name; CL-15 is gone."""
    assert engine.CL19 == "CL-19" and not hasattr(engine, "CL15")
    assert cli.EXIT_INCOMPLETE == engine.EXIT_INCOMPLETE == 4
    categories = engine._STOP_CATEGORIES  # pylint: disable=protected-access
    assert {open_item for _category, open_item in categories.values()} == {"CL-19", None}


@pytest.mark.parametrize("code, category", [
    (ErrorCode.CLONE_FAILED, "clone failure"),
    (ErrorCode.NETWORK, "network failure"),
])
def test_clone_and_network_stops_end_with_exit_4(code: ErrorCode, category: str) -> None:
    """FR-48 (Freeze rev. 116): a unit stopped on a clone or network error is incomplete."""
    stop = engine.UnitStopped.wrap(FX_RG1_URL, TdeaError(code, "Type", "cause.", "action."))
    assert (stop.category, stop.open_item, stop.exit_code) == (category, None, 4)


@pytest.mark.parametrize("code, category, open_item", [
    *[(code, "failed accessibility check", "CL-19") for code in (
        ErrorCode.NOT_FOUND, ErrorCode.NOT_ACCESSIBLE, ErrorCode.PRIVATE_REPOSITORY,
        ErrorCode.EMPTY_REPOSITORY, ErrorCode.AUTH_FAILED, ErrorCode.RATE_LIMITED,
        ErrorCode.GITHUB_ERROR)],
    (ErrorCode.HISTORY_FAILED, "history read failure", None),
])
def test_each_stop_is_categorised_without_an_exit_code(code: ErrorCode, category: str,
                                                       open_item: str | None) -> None:
    """Day 6 reading: the category and open item of each stop; none gets an exit code."""
    stop = engine.UnitStopped.wrap(FX_RG1_URL, TdeaError(code, "Type", "cause.", "action."))
    assert (stop.category, stop.open_item, stop.exit_code) == (category, open_item, None)
    assert stop.cause == f"{FX_RG1_URL}: cause."
    wrapped = ResearchRunError.wrap(stop)
    assert (wrapped.category, wrapped.open_item, wrapped.exit_code) == (category, open_item, None)
