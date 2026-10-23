"""RE-01 step 3 (FR-48, NFR-11): bad run files exit 1 before any clone, with three-part errors.

The three run files of RE-01 step 3 (no W, overlapping windows, a token) go through the
real command line in process. "Before any clone" is made concrete: every operation that
could clone, reach GitHub, or create the key file, the research store, the lint cache or
any other file is replaced by a tripwire for the test, and the home and working folders
stay empty. The network guard of tests/conftest.py refuses every external host anyway.
These checks need no Pylint, so they run in every CI cell. The other RE-01 steps are in
test_re01_refusals.py and tests/research/test_re01_run.py (Day 6).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from run_files import schema_example

from tdea.aggregator import key_store, lint_cache, research_store
from tdea.api.cli import EXIT_INVALID_INPUT, EXIT_OK, main
from tdea.git_handler import github_client, local_repository

VALID = schema_example()


@pytest.fixture(name="sealed")
def _sealed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run in empty home and working folders with every side effect made to fail."""
    home, work = tmp_path / "home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(work)

    def tripwire(*_args: object, **_kwargs: object) -> None:
        """Fail the test: validate must not do this."""
        raise AssertionError("validate attempted a side effect")

    for owner, name in ((local_repository.LocalRepository, "clone"),
                        (local_repository.LocalRepository, "__init__"),
                        (github_client.GitHubClient, "check_repository"),
                        (key_store.KeyStore, "__init__"),
                        (research_store.ResearchStore, "__init__"),
                        (lint_cache.LintCache, "__init__")):
        monkeypatch.setattr(owner, name, tripwire)
    return tmp_path


def run_file(folder: Path, text: str) -> str:
    """Write a run file outside the sealed folders and return its path."""
    path = folder / "run.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def files_under(*roots: Path) -> list[Path]:
    """Return every file and folder under ``roots``."""
    return sorted(path for root in roots for path in root.rglob("*"))


@pytest.mark.parametrize("name, text, cause", [
    ("without W", VALID.replace("w_months: 6\n", ""),
     "W is missing: the key 'w_months' is required."),
    ("overlapping windows", VALID.replace("end: 2025-07-01}", "end: 2025-07-15}"),
     "Repository 1: windows 1 and 2 overlap."),
])
def test_step3_rejects_before_any_clone_with_three_part_errors(
    sealed: Path, capsys: pytest.CaptureFixture[str], name: str, text: str, cause: str
) -> None:
    """No W or overlapping windows: exit 1, Error/Cause/Action printed, nothing touched."""
    assert main(["research", "validate", run_file(sealed, text)]) == EXIT_INVALID_INPUT, name
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines() == [
        "Error: Invalid run file", f"Cause: {cause}",
        next(line for line in output.err.splitlines() if line.startswith("Action: "))]
    assert not files_under(sealed / "home", sealed / "work")


def test_step3_rejects_a_token_without_printing_or_storing_it(
    sealed: Path, capsys: pytest.CaptureFixture[str], canary_token: str,
    find_canary: object,
) -> None:
    """A run file with a token: exit 1 before any clone; the token appears nowhere."""
    path = run_file(sealed, VALID.replace("cutoff: null", f"cutoff: null  # {canary_token}"))
    assert main(["research", "validate", path]) == EXIT_INVALID_INPUT
    output = capsys.readouterr()
    assert output.err.splitlines()[0] == "Error: Run file contains a token"
    assert canary_token not in output.out + output.err
    assert not files_under(sealed / "home", sealed / "work")
    assert find_canary(sealed / "home", sealed / "work") == []


def test_a_valid_run_file_exits_0_and_touches_nothing(
    sealed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The schema example validates with exit 0, a one-line summary and no side effect."""
    assert main(["research", "validate", run_file(sealed, VALID)]) == EXIT_OK
    output = capsys.readouterr()
    assert output.out == ("Run file is valid: an RG1 run over 1 repository and 2 units "
                          "(one per repository and window).\n")
    assert output.err == "" and not files_under(sealed / "home", sealed / "work")


def test_several_problems_are_printed_as_separate_three_part_blocks(
    sealed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each problem gets its own Error/Cause/Action block, separated by a blank line."""
    text = VALID.replace("w_months: 6\n", "").replace("tau: 0.7", "tau: 0.9")
    assert main(["research", "validate", run_file(sealed, text)]) == EXIT_INVALID_INPUT
    blocks = capsys.readouterr().err.strip().split("\n\n")
    assert len(blocks) == 2
    for block in blocks:
        assert [line.split(":", 1)[0] for line in block.splitlines()] == [
            "Error", "Cause", "Action"]


def test_a_missing_run_file_is_invalid_input(
    sealed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path with no file exits 1 with a three-part error, not a traceback."""
    assert main(["research", "validate", str(sealed / "absent.yaml")]) == EXIT_INVALID_INPUT
    assert capsys.readouterr().err.startswith("Error: Run file cannot be read\nCause: ")


@pytest.mark.parametrize("argv", [["research", "validate"],
                                  ["research", "validate", "a.yaml", "b.yaml"],
                                  ["research", "run"], ["research", "status"],
                                  ["research", "run", "a.yaml", "--token", "x"],
                                  ["research", "export", "a.yaml"]])
def test_usage_errors_exit_1_and_export_does_not_exist(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed command line is invalid input; export is not part of Day 6 (FR-62)."""
    assert main(argv) == EXIT_INVALID_INPUT
    assert "error:" in capsys.readouterr().err


def test_help_lists_validate_run_status_and_export(
        capsys: pytest.CaptureFixture[str]) -> None:
    """tdea research --help names validate, run, status and export (FR-48, FR-62)."""
    assert main(["research", "--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "validate" in out and "check a run file without side effects" in out
    assert "run" in out and "status" in out and "export the records of complete runs" in out
    assert main(["research"]) == EXIT_OK
    assert main([]) == EXIT_OK
