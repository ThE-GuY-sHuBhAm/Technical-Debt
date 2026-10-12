"""Fail-closed stops of FR-56 roll back their whole unit (Task 7 Day 7 decision P6).

SYNTHETIC — not CL-03 (run_support doubles). For every fail-closed cause, a run of two
units on a small synthetic history is checked: unit 1 completes and keeps all its
records; unit 2 hits the cause and leaves no unit row, debt item, disappearance, item
mode, base rate or unit summary; the clone is deleted; the error is printed in three
parts. The FR-56 stops use Day 6's unresolved-code path (the undecided note and
placeholder status 1; no exit code is mapped for them), and a CL-03 refusal during a unit
keeps its agreed exit 3. A commit without a known trace status stops the first unit that
runs, before anything is stored, because a repository's history is stored with its first
unit. Two causes Git cannot produce on its own (an unreadable linted version, two changes
of one path in a commit) are simulated by patching the step that detects them; the tests
say so. The run row stays "running" after a stop (the documented Day 6 limitation).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fixture_git import FixtureRepo
from make_fx_rg1 import ASHA, JANE, RAVI
from run_support import (FX_RG1_URL, LocalAccess, SyntheticBulkRules, SyntheticClassifier,
                         SyntheticPackages, Workspace, rows, run_file_text)

from tdea.analyzer.research.rg1 import disappearance, unit_run
from tdea.analyzer.research.rg1.bulk import BulkRulesUnresolved
from tdea.analyzer.research.rg1.fail_closed import change_inconsistent
from tdea.analyzer.research.rg1.provenance import ListVersion, Provenance
from tdea.analyzer.research.rg1.signals import SourceUnreadable
from tdea.analyzer.research.engine import Rg1Rules
from tdea.api import cli
from tdea.api.cli import EXIT_REFUSED, main

FIRST = '"""First."""\n\n\ndef first():\n    """Return a value."""\n    return f"first"\n'
SECOND = '"""Second."""\n\n\ndef second():\n    """Return a value."""\n    return f"second"\n'
MODULE_LEVEL = '"""Second."""\n\nLABEL = f"second"\n'
TWO_LAMBDAS = '"""Second."""\n\nPAIR = (lambda: f"left", lambda: f"right")\n'
WINDOWS = ("{start: 2026-01-01, end: 2026-01-10}", "{start: 2026-02-01, end: 2026-02-10}")
UNIT_1_COUNTS = {"unit": 1, "debt_item": 1, "disappearance": 1, "item_mode": 4,
                 "base_rate": 1, "unit_summary": 4}


class UnknownClassifier(SyntheticClassifier):
    """SYNTHETIC — not CL-03: the listed subjects get no known trace status."""

    def __init__(self, unknown: tuple[str, ...]) -> None:
        """Leave ``unknown`` subjects without a status."""
        super().__init__()
        self.unknown = unknown

    def classify(self, raw):
        """Return a provenance whose status is unknown for the listed subjects."""
        found = super().classify(raw)
        if raw.message.split("\n", 1)[0].strip() in self.unknown:
            return Provenance(None, None, (), None, found.lists)  # type: ignore[arg-type]
        return found


class RefusingBulk(SyntheticBulkRules):
    """SYNTHETIC — not CL-03: claims complete lists, but refuses for the second module."""

    def config_changed(self, changes):
        """Refuse as CL-03 does when a change touches pkg/second.py."""
        if any(side is not None and side.path == "pkg/second.py"
               for change in changes for side in (change.old, change.new)):
            raise BulkRulesUnresolved("SYNTHETIC refusal standing in for CL-03")
        return super().config_changed(changes)

    def list_versions(self):
        """Report a complete list, so the gate before the run passes."""
        return (ListVersion("SYNTHETIC — not CL-03: bulk lists", "test", True, "0" * 64),)


@dataclass(frozen=True)
class Case:
    """One fail-closed cause: the second module, its fix, the rules and the expected error."""

    second: str
    fixed: str
    error_type: str
    category: str | None  # None: the CL-03 refusal, exit 3
    classifier: SyntheticClassifier | None = None
    bulk: SyntheticBulkRules | None = None
    touch: str | None = None  # an edit before the fix, keeping the warning


PLAIN = Case(SECOND, SECOND.replace('f"second"', '"second"'), "", "")
CASES = {
    "cl17_module_level": Case(MODULE_LEVEL, MODULE_LEVEL.replace('f"second"', '"second"'),
                              "Disappearance mode undecided",
                              "unsupported module-level classification (CL-17 open)"),
    "unlocatable_scope": Case(TWO_LAMBDAS, TWO_LAMBDAS.replace('f"left"', '"left"'),
                              "Item scope not located", "unlocatable item scope"),
    "cl03_bulk_refusal": Case(SECOND, SECOND.replace('f"second"', '"second"'),
                              "Research run refused", None, bulk=RefusingBulk()),
}


def history(path: Path, case: Case) -> FixtureRepo:
    """Unit 1: first, introduced and fixed; unit 2: the case's second module."""
    repo = FixtureRepo(path)
    repo.commit("Add first", {"pkg/first.py": FIRST}, author=ASHA,
                date="2026-01-05T09:00:00+00:00")
    repo.commit("Fix first", {"pkg/first.py": FIRST.replace('f"first"', '"first"')},
                author=RAVI, date="2026-01-20T09:00:00+00:00")
    repo.commit("Add second", {"pkg/second.py": case.second}, author=JANE,
                date="2026-02-05T09:00:00+00:00")
    if case.touch is not None:
        repo.commit("Touch second", {"pkg/second.py": case.touch}, author=RAVI,
                    date="2026-02-15T09:00:00+00:00")
    if case.fixed != case.second:
        repo.commit("Fix second", {"pkg/second.py": case.fixed}, author=ASHA,
                    date="2026-02-20T09:00:00+00:00")
    return repo


def run(tmp_path: Path, case: Case, windows: tuple[str, ...] = WINDOWS) -> tuple[int, Workspace]:
    """Run the case's history; return the exit status and the workspace."""
    repo = history(tmp_path / "repo", case)
    workspace = Workspace(tmp_path / "ws", LocalAccess({"tdea-fixtures/fx-rg1": repo.path}))
    workspace.root.mkdir()
    rules = Rg1Rules(case.classifier or SyntheticClassifier(),
                     SyntheticPackages(("pkg/first.py", "pkg/second.py")),
                     case.bulk or SyntheticBulkRules(()))
    text = run_file_text([(FX_RG1_URL, windows, None)], cutoff="2026-10-01T00:00:00Z")
    return main(["research", "run", str(workspace.run_file(text))],
                service=workspace.service(rules)), workspace


def assert_only_unit_1(workspace: Workspace) -> None:
    """Unit 1's records are all there; nothing of unit 2 is."""
    for table, count in UNIT_1_COUNTS.items():
        assert rows(workspace.store, f"SELECT count(*) FROM {table}") == [(count,)], table
    assert rows(workspace.store, "SELECT path FROM debt_item") == [("pkg/first.py",)]
    assert rows(workspace.store, "SELECT window_start FROM unit") == [
        ("2026-01-01T00:00:00+00:00",)]
    assert rows(workspace.store, "SELECT status FROM run") == [("running",)]
    assert not workspace.clone_entries()


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_fail_closed_cause_rolls_back_its_whole_unit(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P6: the failing unit leaves no record; unit 1 stays; three parts are printed."""
    case = CASES[name]
    code, workspace = run(tmp_path, case)
    err = capsys.readouterr().err
    assert err.startswith(f"Error: {case.error_type}\nCause: ")
    if case.category is None:
        assert code == EXIT_REFUSED and cli.PLACEHOLDER_NOTE not in err
    else:
        assert code == cli.EXIT_UNDECIDED and cli.PLACEHOLDER_NOTE in err
        assert (f"Exit code unresolved for this {case.category}: no source defines its exit "
                "code.") in err
    assert_only_unit_1(workspace)


def test_a_stop_in_the_first_unit_stores_nothing_of_the_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no unit stored, neither the repository row nor its history is written."""
    code, workspace = run(tmp_path, CASES["cl17_module_level"], WINDOWS[1:])
    assert code == cli.EXIT_UNDECIDED and "CL-17" in capsys.readouterr().err
    for table in ("repository", "commit", "file_change", *UNIT_1_COUNTS):
        assert rows(workspace.store, f'SELECT count(*) FROM "{table}"') == [(0,)], table


@pytest.mark.parametrize(("subject", "fixed"), [
    ("Fix second", True),  # c1 of the second item: its resolver
    ("Touch second", False),  # a later touch: the base rate of a censored item
])
def test_unknown_provenance_stops_the_first_unit_and_stores_nothing(
    subject: str, fixed: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """R5 condition 7: an unknown status fails closed before the history is stored."""
    case = Case(SECOND, SECOND.replace('f"second"', '"second"') if fixed else SECOND,
                "Provenance unknown", "unknown provenance",
                classifier=UnknownClassifier((subject,)), touch=SECOND + "EXTRA = 1\n")
    code, workspace = run(tmp_path, case)
    err = capsys.readouterr().err
    assert code == cli.EXIT_UNDECIDED and err.startswith("Error: Provenance unknown\nCause: ")
    assert "Exit code unresolved for this unknown provenance: no source defines" in err
    for table in ("repository", "commit", "file_change", *UNIT_1_COUNTS):
        assert rows(workspace.store, f'SELECT count(*) FROM "{table}"') == [(0,)], table
    assert not workspace.clone_entries()


def patched(monkeypatch: pytest.MonkeyPatch, module: object, name: str,
            wrap: Callable) -> None:
    """Replace ``module.name`` with ``wrap(original)``."""
    monkeypatch.setattr(module, name, wrap(getattr(module, name)))


@pytest.mark.parametrize("side", [b'f"second"', b'return "second"'])
def test_simulated_an_unreadable_version_rolls_back_its_unit(
    side: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """Simulated (a linting version always tokenises): the version before c1, or at c1, fails."""
    def wrap(original):
        def tokens(content: bytes):
            if side in content:
                raise SourceUnreadable("simulated")
            return original(content)
        return tokens

    patched(monkeypatch, disappearance, "python_tokens", wrap)
    code, workspace = run(tmp_path, PLAIN)
    err = capsys.readouterr().err
    assert code == cli.EXIT_UNDECIDED and err.startswith("Error: File version unreadable\n")
    assert "pkg/second.py" in err and "Exit code unresolved for this unreadable file version" in err
    assert_only_unit_1(workspace)


def test_simulated_an_inconsistent_file_change_rolls_back_its_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Simulated (Git lists one change per path): the base rate meets two for pkg/second.py."""
    def wrap(original):
        def rate(introduction, records, w_months):
            if introduction.path == "pkg/second.py":
                raise change_inconsistent("simulated", introduction.path)
            return original(introduction, records, w_months)
        return rate

    patched(monkeypatch, unit_run, "base_rate", wrap)
    code, workspace = run(tmp_path, PLAIN)
    err = capsys.readouterr().err
    assert code == cli.EXIT_UNDECIDED and err.startswith("Error: Inconsistent file change\n")
    assert "Exit code unresolved for this inconsistent file change" in err
    assert_only_unit_1(workspace)
