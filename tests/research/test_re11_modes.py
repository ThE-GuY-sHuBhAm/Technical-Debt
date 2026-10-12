"""RE-11 (FR-56): disappearance modes and tau variants on FX-RG1 v3.

SYNTHETIC — not CL-03. Every run here uses run_support's test doubles for provenance, the
package filter and the D-4 bulk lists, because a real run is refused while CL-03 is
incomplete. The a1 and a3 cases (config_fix, message_fix) are mechanics only: the double
lists their configuration path and subject. Results here show that the classifier applies
FR-56's precedence and the Day 7 readings to each fixture case; they validate nothing
about the research methodology and are not RE-11 evidence for a real run.

What stays BLOCKED (strict expected failures at the end): RE-11 as written, a run of
FX-RG1 (CL-03 refuses it); the a1 and a3 cases with the CL-03 v0.1 lists (no matching
rule exists); and the module-level case, which RE-11 expects to be a local fix while the
unit fails closed until CL-17 is decided.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1_v3 as v3
import pytest
from handmade import Entry, Record, provenance
from run_support import (FX_RG1_PACKAGES, FX_RG1_URL, PINNED_FACTS, LocalAccess,
                         SyntheticBulkRules, SyntheticClassifier, SyntheticPackages,
                         TickingClock, Workspace, rows, run_file_text, synthetic_rules)

from tdea.aggregator.research_service import ResearchService
from tdea.analyzer.research.engine import ResearchEngine, Rg1Rules
from tdea.analyzer.research.profile import LintResult, ResearchProfile
from tdea.analyzer.research.rg1.bulk import Cl03BulkRules
from tdea.analyzer.research.rg1.disappearance import Counterpart, DisappearanceClassifier
from tdea.analyzer.research.rg1.fail_closed import ClassificationStopped
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import occurrences
from tdea.analyzer.research.rg1.provenance import TraceStatus
from tdea.analyzer.research.rg1.tracking import Tracking
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk
from tdea.api import cli
from tdea.api.cli import EXIT_OK, main

TAUS = (0.5, 0.6, 0.7, 0.8)
EXPECTED_MODES = {  # case -> modes at tau 0.5, 0.6, 0.7, 0.8
    "sweep_item": "aaaa", "bulk_fix": "aaaa", "message_fix": "aaaa", "config_fix": "aaaa",
    "removed": "bbbb", "deleted": "cccc", "rewrite": "eddd", "local": "eeee",
    "broken": "ffff", "artifact": "ffff",
}


@dataclass(frozen=True)
class V3Run:
    """A finished run of FX-RG1 v3 and its workspace."""

    workspace: Workspace
    code: int


def v3_service(workspace: Workspace, rules: Rg1Rules | None = None) -> ResearchService:
    """Return a service whose clone and run clocks stand at FX-RG1 v3's clone time."""
    engine = ResearchEngine(rules if rules is not None else synthetic_rules(), None,
                            workspace.access, clock=lambda: v3.CLONE_TIME)
    return ResearchService(engine, TickingClock(v3.CLONE_TIME), paths=workspace.paths(),
                           environment=PINNED_FACTS)


def v3_workspace(root: Path, fixture: v3.FxRg1V3, cache: Path | None = None) -> Workspace:
    """Return a workspace for FX-RG1 v3, with a copy of ``cache`` as its lint cache."""
    root.mkdir(parents=True)
    workspace = Workspace(root, LocalAccess({"tdea-fixtures/fx-rg1": fixture.path}))
    if cache is not None:
        target = workspace.paths().lint_cache
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache, target)
    return workspace


def v3_run_file(*windows: str) -> str:
    """Return an FX-RG1 v3 run file over ``windows``, W = 6, with the fixture's cut-off."""
    return run_file_text([(FX_RG1_URL, windows, None)], cutoff="2027-06-01T00:00:00Z")


@pytest.fixture(name="modes_run", scope="module")
def _modes_run(fx_rg1_v3: v3.FxRg1V3, tmp_path_factory: pytest.TempPathFactory) -> V3Run:
    """Run the modes window of FX-RG1 v3 once (SYNTHETIC — not CL-03)."""
    workspace = v3_workspace(tmp_path_factory.mktemp("re11") / "ws", fx_rg1_v3)
    code = main(["research", "run", str(workspace.run_file(v3_run_file(v3.MODES_WINDOW)))],
                service=v3_service(workspace))
    return V3Run(workspace, code)


def by_case(store: Path, query: str) -> dict[str, tuple]:
    """Return ``query``'s rows (first column the item's path) keyed by fixture case."""
    cases = {path: name for name, (path, *_rest) in v3.CASES.items()}
    return {cases[row[0]]: row[1:] for row in rows(store, query)}


def test_fx_rg1_v3_keeps_every_v1_and_v2_commit(fx_rg1_v3: v3.FxRg1V3,
                                                fx_rg1_v2) -> None:
    """Day 7 decision: v3 is appended to v2; v1's and v2's SHAs are unchanged."""
    assert fx_rg1_v3.v2_shas == fx_rg1_v2.shas
    assert fx_rg1_v3.shas.items() >= fx_rg1_v2.shas.items()
    assert set(fx_rg1_v3.shas) - set(fx_rg1_v2.shas) == set(v3.FIRST_PARENT_V3)


def test_fx_rg1_v3_is_identical_on_every_build(fx_rg1_v3: v3.FxRg1V3, tmp_path: Path) -> None:
    """The v3 builder is deterministic, like v1's and v2's."""
    assert v3.build(tmp_path / "again").shas == fx_rg1_v3.shas


def test_synthetic_each_case_gets_its_mode_at_each_tau(modes_run: V3Run) -> None:
    """SYNTHETIC — not CL-03: one disappearance per mode; the 0.55 rewrite flips at 0.6."""
    assert modes_run.code == EXIT_OK
    cases = {path: name for name, (path, *_rest) in v3.CASES.items()}
    modes: dict[str, str] = {}
    for path, mode in rows(modes_run.workspace.store,
                           "SELECT d.path, m.mode FROM debt_item d JOIN item_mode m "
                           "USING (item_id) ORDER BY d.path, m.tau"):
        modes[cases[path]] = modes.get(cases[path], "") + mode
    assert modes == EXPECTED_MODES


def test_synthetic_signals_are_stored_with_null_only_when_not_computable(
    modes_run: V3Run
) -> None:
    """All FR-56 signals; null (not false or 0) when a signal cannot be computed (P3)."""
    signals = by_case(modes_run.workspace.store,
                      "SELECT d.path, x.bulk_a1, x.bulk_a2, x.bulk_a3, x.file_removed, "
                      "x.parse_failure_reason, x.counterpart_method, x.counterpart_scope, "
                      "x.similarity, x.touch_exact, x.touch_near, x.touch_scope "
                      "FROM debt_item d JOIN disappearance x USING (item_id)")
    assert signals["sweep_item"] == (0, 1, 0, 0, None, "same name", "swept", 1.0, 1, 1, 1)
    assert signals["bulk_fix"][:4] == (0, 1, 0, 0)  # a2, though the fix is not formatting
    assert signals["config_fix"][:4] == (1, 0, 0, 0)
    assert signals["message_fix"][:4] == (0, 0, 1, 0)
    assert signals["removed"] == (0, 0, 0, 1, None, None, None, None, 1, 1, 1)
    assert signals["deleted"] == (0, 0, 0, 0, None, None, None, None, 1, 1, 1)
    assert signals["rewrite"] == (0, 0, 0, 0, None, "same name", "shaped", 0.55, 1, 1, 1)
    assert signals["local"] == (0, 0, 0, 0, None, "same name", "local", 0.9, 1, 1, 1)
    assert signals["broken"] == (0, 0, 0, 0, "syntax error", None, None, None, 1, 1, 1)
    artifact = signals["artifact"]
    assert artifact[5:7] == ("same name", "unused") and artifact[7] > 0.8
    assert artifact[8:] == (0, 1, 1)  # false, not null: the line itself is not touched


def test_synthetic_modes_are_unvalidated_and_local_fix_means_mode_e(modes_run: V3Run) -> None:
    """Every mode carries unvalidated = true; the local-fix flag is set exactly for mode e."""
    found = rows(modes_run.workspace.store, "SELECT mode, local_fix, unvalidated, tau "
                                            "FROM item_mode")
    assert len(found) == 4 * len(EXPECTED_MODES)
    assert {row[3] for row in found} == set(TAUS)
    assert all(unvalidated == 1 for _mode, _fix, unvalidated, _tau in found)
    assert all(fix == (mode == "e") for mode, fix, _unvalidated, _tau in found)


def test_synthetic_local_fix_share_per_unit_and_tau(modes_run: V3Run) -> None:
    """Ten disappearances; local fixes: rewrite and local at 0.5, local alone above."""
    assert rows(modes_run.workspace.store, "SELECT tau, disappearances, local_fixes, "
                                           "local_fix_share FROM unit_summary ORDER BY tau") == [
        (0.5, 10, 2, 0.2), (0.6, 10, 1, 0.1), (0.7, 10, 1, 0.1), (0.8, 10, 1, 0.1)]


def test_synthetic_resolver_and_flags_compare_identity_keys(modes_run: V3Run) -> None:
    """c1's status and tool (the double gives none); flags from keys of c and c1 (ASHA)."""
    resolver = by_case(modes_run.workspace.store,
                       "SELECT d.path, x.resolver_trace_status, x.resolver_tool, x.same_name, "
                       "x.same_email FROM debt_item d JOIN disappearance x USING (item_id)")
    assert resolver["broken"] == ("non_traced", None, 1, 1)  # c and c1 by ASHA
    assert resolver["local"] == ("non_traced", None, 0, 0)  # c1 by JANE


def test_synthetic_every_item_has_a_base_rate_with_the_theoretical_end_of_w(
    modes_run: V3Run
) -> None:
    """Base rate: each file is touched once after its introduction, by c1 (non-traced)."""
    rates = by_case(modes_run.workspace.store,
                    "SELECT d.path, b.ai_traced_count, b.non_traced_count, b.ai_traced_share, "
                    "b.window_end FROM debt_item d JOIN base_rate b USING (item_id)")
    assert set(rates) == set(EXPECTED_MODES)
    assert set(rates.values()) == {(0, 1, 0.0, "2027-05-02T09:00:00+00:00")}


def test_cl17_module_level_case_fails_closed_and_stores_nothing_of_its_unit(
    modes_run: V3Run, fx_rg1_v3: v3.FxRg1V3, tmp_path: Path,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """Decision R9 (B): the CL-17 path stops the unit; unit 1 stays; no mode is guessed."""
    workspace = v3_workspace(tmp_path / "ws", fx_rg1_v3,
                             modes_run.workspace.paths().lint_cache)
    text = v3_run_file(v3.MODES_WINDOW, v3.MODULE_WINDOW)
    assert main(["research", "run", str(workspace.run_file(text))],
                service=v3_service(workspace)) == cli.EXIT_UNDECIDED
    err = capsys.readouterr().err
    assert err.startswith("Error: Disappearance mode undecided\nCause: "
                          f"{FX_RG1_URL}: The module-level item on line 3 of "
                          "pkg/modes/module_level.py disappears at commit "
                          f"{fx_rg1_v3.shas['module_fix']}.")
    assert "CL-17" in err and cli.PLACEHOLDER_NOTE in err
    assert ("Exit code unresolved for this unsupported module-level classification (CL-17 "
            "open): no source defines its exit code.") in err
    store = workspace.store
    assert rows(store, "SELECT count(*) FROM unit") == [(1,)]
    for table, count in (("debt_item", 10), ("disappearance", 10), ("item_mode", 40),
                         ("base_rate", 10), ("unit_summary", 4)):
        assert rows(store, f"SELECT count(*) FROM {table}") == [(count,)], table
    assert rows(store, "SELECT count(*) FROM debt_item WHERE path LIKE '%module_level%'") \
        == [(0,)]
    assert not workspace.clone_entries()


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: CL-17. RE-11 expects the module-level item to be a local "
                          "fix; the unit fails closed until CL-17 is decided (G2)")
def test_re11_module_level_item_is_a_local_fix(
    modes_run: V3Run, fx_rg1_v3: v3.FxRg1V3, tmp_path: Path
) -> None:
    """RE-11: "the module-level item is 'local fix'". Not implemented while CL-17 is open."""
    workspace = v3_workspace(tmp_path / "ws", fx_rg1_v3,
                             modes_run.workspace.paths().lint_cache)
    code = main(["research", "run", str(workspace.run_file(v3_run_file(v3.MODULE_WINDOW)))],
                service=v3_service(workspace))
    assert code == EXIT_OK
    assert rows(workspace.store, "SELECT DISTINCT mode FROM item_mode") == [("e",)]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: CL-03. The D-4 bulk lists v0.1 are incomplete and define "
                          "no matching rule, so the run is refused before any unit")
def test_re11_a1_and_a3_with_the_cl03_lists(fx_rg1_v3: v3.FxRg1V3, tmp_path: Path) -> None:
    """RE-11 with the real bulk lists: the config and message cases would be bulk."""
    workspace = v3_workspace(tmp_path / "ws", fx_rg1_v3)
    rules = Rg1Rules(SyntheticClassifier(), SyntheticPackages(FX_RG1_PACKAGES),
                     Cl03BulkRules())
    code = main(["research", "run", str(workspace.run_file(v3_run_file(v3.MODES_WINDOW)))],
                service=v3_service(workspace, rules))
    assert code == EXIT_OK


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: CL-03. A real run of FX-RG1 is refused while the CL-03 "
                          "lists are incomplete (Task 7 Day 6 decision 1)")
def test_re11_as_written_with_the_real_rules(fx_rg1_v3: v3.FxRg1V3, tmp_path: Path) -> None:
    """RE-11: run FX-RG1 and read the modes, with the real CL-03 components."""
    workspace = v3_workspace(tmp_path / "ws", fx_rg1_v3)
    code = main(["research", "run", str(workspace.run_file(v3_run_file(v3.MODES_WINDOW)))],
                service=v3_service(workspace, Rg1Rules()))
    assert code == EXIT_OK


# --- Hand-made inputs: paths a fixture run cannot reach -------------------------------------

class Blobs:
    """A ClassifierSource over hand-made blobs, linted with the research profile."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        """Hold the blobs by name."""
        self.blobs = blobs

    def read_blob(self, blob: str) -> bytes:
        """Return a blob."""
        return self.blobs[blob]

    def lint(self, content: bytes, path: str, _blob: str) -> LintResult:
        """Lint as the research profile does."""
        return ResearchProfile().lint(content, Path(path).name)


BEFORE = b'"""Doc."""\n\n\ndef value():\n    """Return a value."""\n    return f"x"\n'
MOMENT = datetime(2026, 1, 5, 9, tzinfo=timezone.utc)


def hand_made(c1_status: TraceStatus | None, *changes: FileChange,
              keys: tuple[str | None, str | None] = ("a" * 64, None),
              versions: tuple[bytes, bytes] = (BEFORE, BEFORE.replace(b'f"x"', b'"x"')),
              bulk: SyntheticBulkRules | None = None
              ) -> tuple[DisappearanceClassifier, Introduction, Tracking]:
    """Return a classifier, the item (the first W1309 of ``versions[0]``) and its tracking.

    c1 is "c1", with ``changes`` and the message "Fix"; blob "b0" is the version before
    c1 and "b1" the version at c1.
    """
    source = Blobs({"b0": versions[0], "b1": versions[1]})
    occurrence = next(found for found in occurrences(
        source.lint(versions[0], "pkg/m.py", "b0").messages, versions[0])
        if found.key.symbol == "f-string-without-interpolation")
    introduction = Introduction("c", False, MOMENT, "pkg/m.py", "b0", occurrence.key,
                                occurrence.message.message_id, occurrence.message.category,
                                occurrence.message.message, occurrence.line, 0)
    tracking = Tracking(MOMENT, c1_sha="c1", c1_date=MOMENT, path_before_c1="pkg/m.py",
                        blob_before_c1="b0", line_before_c1=occurrence.line)
    commits = {
        "c": Record("c", author_name_key="a" * 64, author_email_key="b" * 64),
        "c1": Record("c1", provenance=provenance(c1_status), author_name_key=keys[0],
                     author_email_key=keys[1], changes=tuple(map(Entry, changes)),
                     message="Fix"),
    }
    rules = bulk if bulk is not None else SyntheticBulkRules(())
    return DisappearanceClassifier(source, rules, commits), introduction, tracking


MODIFY = FileChange(ChangeType.MODIFIED, FileSide("pkg/m.py", "b0"), FileSide("pkg/m.py", "b1"),
                    None, False, (Hunk(6, 1, 6, 1),))


def test_hand_made_flags_are_null_when_a_key_is_null() -> None:
    """Same-name compares name keys; a null e-mail key gives a null flag, not false."""
    classifier, introduction, tracking = hand_made(TraceStatus.AI_TRACED, MODIFY)
    found = classifier.classify(introduction, tracking)
    assert (found.same_name, found.same_email) == (True, None)
    assert found.resolver_trace_status == "ai_traced" and found.modes[0] == (0.5, "e")


def test_hand_made_unknown_c1_provenance_fails_closed() -> None:
    """The resolver needs c1's status; an unknown one is never read as non-traced."""
    classifier, introduction, tracking = hand_made(None, MODIFY)
    with pytest.raises(ClassificationStopped) as stopped:
        classifier.classify(introduction, tracking)
    assert stopped.value.category == "unknown provenance"


def test_hand_made_a_binary_deletion_is_removed_with_null_touches() -> None:
    """A deletion Git diffs as binary has no hunks: mode b, touches null (P3), not false."""
    deletion = FileChange(ChangeType.DELETED, FileSide("pkg/m.py", "b0"), None, None, True, ())
    classifier, introduction, tracking = hand_made(TraceStatus.NON_TRACED, deletion)
    found = classifier.classify(introduction, tracking)
    assert found.touch is None and found.file_removed
    assert found.counterpart == Counterpart() and {mode for _tau, mode in found.modes} == {"b"}


def test_hand_made_an_item_without_c1_has_no_disappearance() -> None:
    """Only an item with c1 is classified."""
    classifier, introduction, tracking = hand_made(TraceStatus.NON_TRACED, MODIFY)
    with pytest.raises(ValueError):
        classifier.classify(introduction, Tracking(MOMENT, censoring_date=MOMENT,
                                                   censoring_reason="window"))
    classifier, introduction, tracking = hand_made(TraceStatus.NON_TRACED, MODIFY, MODIFY)
    with pytest.raises(ClassificationStopped) as stopped:
        classifier.classify(introduction, tracking)
    assert stopped.value.category == "inconsistent file change"


DELETE = FileChange(ChangeType.DELETED, FileSide("pkg/m.py", "b0"), None, None, False,
                    (Hunk(1, 6, 0, 0),))


def modes(classifier: DisappearanceClassifier, introduction: Introduction,
          tracking: Tracking) -> str:
    """Return the modes at tau 0.5, 0.6, 0.7 and 0.8 as one string."""
    return "".join(mode for _tau, mode in classifier.classify(introduction, tracking).modes)


def test_hand_made_bulk_comes_before_file_removed() -> None:
    """Precedence: a commit that is bulk (a3, SYNTHETIC double) and removes the file is (a)."""
    found = hand_made(TraceStatus.NON_TRACED, DELETE,
                      bulk=SyntheticBulkRules((), subjects=("Fix",)))
    assert modes(*found) == "aaaa"
    assert modes(*hand_made(TraceStatus.NON_TRACED, DELETE)) == "bbbb"


def test_hand_made_a_module_level_item_whose_file_fails_to_lint_is_an_artifact() -> None:
    """Precedence: (f) for a lint failure at c1 comes before the CL-17 module-level stop."""
    found = hand_made(TraceStatus.NON_TRACED, MODIFY.__class__(
        ChangeType.MODIFIED, FileSide("pkg/m.py", "b0"), FileSide("pkg/m.py", "b1"), None,
        False, (Hunk(1, 1, 1, 1),)), versions=(b'LABEL = f"x"\n', b'LABEL = "x"(\n'))
    assert found[1].scope == "" and modes(*found) == "ffff"
    stopped = hand_made(TraceStatus.NON_TRACED, MODIFY.__class__(
        ChangeType.MODIFIED, FileSide("pkg/m.py", "b0"), FileSide("pkg/m.py", "b1"), None,
        False, (Hunk(1, 1, 1, 1),)), versions=(b'LABEL = f"x"\n', b'LABEL = "x"\n'))
    with pytest.raises(ClassificationStopped) as stop:
        modes(*stopped)
    assert "CL-17" in stop.value.category


def test_hand_made_similarity_equal_to_tau_is_not_rewritten() -> None:
    """(d) needs similarity below tau: exactly 0.8 at tau 0.8 is a local fix."""
    before, after = b'def f(a):\n    return f"x", a\n', b'def f(a):\n    return "x", b\n'
    change = MODIFY.__class__(ChangeType.MODIFIED, FileSide("pkg/m.py", "b0"),
                              FileSide("pkg/m.py", "b1"), None, False, (Hunk(2, 1, 2, 1),))
    found = hand_made(TraceStatus.NON_TRACED, change, versions=(before, after))
    assert found[0].classify(found[1], found[2]).counterpart.similarity == 0.8
    assert modes(*found) == "eeee"
