"""RE-18: smell counts on added lines and rates on FX-RG10 v2 (FR-61; sprint 23 Oct).

Reads the evaluation rg10_run.evaluate stored and checks it against counts made by hand:
task 1's RE-18 output adds one bare ``except:`` (W0702), one ``except Exception`` (W0718)
and one ``.format()`` call (C0209) in the T1 injected variant, whose helper already has a
W0702 above the marker; task 1 declares 2 T1 and 1 T2 opportunities, task 2 declares
0 T2. Component tests pin ResearchProfile.lint_in_tree: the smell mapping's rules are
enabled on top of the profile, a variant's own Pylint configuration has no effect, and
the target must be a relative path inside the tree.

FX-RG10 v2 is SYNTHETIC: mechanics evidence only. CL-18 (which outputs are linted) is
open; the provisional reading is in tdea/analyzer/research/rg10/counts.py.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import make_fx_rg10_v2
import pytest
from make_fx_rg10 import FxRg10
from rg10_run import Rg10Evaluation
from run_support import rows

from tdea.analyzer.research.profile import OK, ResearchProfile

DOCSTRINGLESS = b"def total(prices):\n    return sum(prices)\n"


def test_task_1_gives_t1_2_and_t2_1_with_rates_1(rg10_evaluated: Rg10Evaluation,
                                                  fx_rg10_v2: FxRg10) -> None:
    """Steps 2-3: T1 = 2 and T2 = 1 on added lines, rates 2/2 = 1.0 and 1/1 = 1.0."""
    gen_id = fx_rg10_v2.gen_ids["smells"]
    assert rows(rg10_evaluated.store, "SELECT smell_type, count, declared_opportunities, "
                "is_target, rate, exclusion_reason FROM type_count WHERE gen_id = ? "
                "ORDER BY smell_type", gen_id) == [("T1", 2, 2, 1, 1.0, None),
                                                   ("T2", 1, 1, 0, 1.0, None)]


def test_the_w0702_above_the_marker_is_not_counted(rg10_evaluated: Rg10Evaluation,
                                                   fx_rg10_v2: FxRg10) -> None:
    """The variant's own bare except is a detection off the added lines, so not counted."""
    gen_id = fx_rg10_v2.gen_ids["smells"]
    found = rows(rg10_evaluated.store, "SELECT message_id, on_added_line FROM detection "
                 "WHERE gen_id = ? AND message_id IN ('W0702', 'W0718', 'C0209') "
                 "ORDER BY line", gen_id)
    assert found == [("W0702", 0), ("W0702", 1), ("C0209", 1), ("W0718", 1)]
    ((first, last),) = rows(rg10_evaluated.store, "SELECT inserted_first_line, "
                            "inserted_last_line FROM output WHERE gen_id = ?", gen_id)
    for line, added in rows(rg10_evaluated.store, "SELECT line, on_added_line FROM detection "
                            "WHERE gen_id = ?", gen_id):
        assert bool(added) == (first <= line <= last)


def test_task_2_has_no_t2_rate(rg10_evaluated: Rg10Evaluation) -> None:
    """Task 2 declares 0 T2 opportunities: no T2 rate, with the reason recorded."""
    found = rows(rg10_evaluated.store, "SELECT t.rate, t.exclusion_reason FROM type_count t "
                 "JOIN output o ON o.bundle_id = t.bundle_id AND o.gen_id = t.gen_id "
                 "WHERE o.task_id = 'task-2' AND t.smell_type = 'T2'")
    assert found and set(found) == {(None, "no declared opportunities")}


def test_the_non_insertable_output_has_no_rate_and_fails(rg10_evaluated: Rg10Evaluation,
                                                         fx_rg10_v2: FxRg10) -> None:
    """The import-error output: counts kept, no rate, both suites failed by rule."""
    gen_id = fx_rg10_v2.gen_ids["import_missing"]
    assert rows(rg10_evaluated.store, "SELECT smell_type, count, rate, exclusion_reason FROM "
                "type_count WHERE gen_id = ? ORDER BY smell_type", gen_id) == [
                    ("T1", 0, None, "not insertable"), ("T2", 0, None, "not insertable")]
    assert rows(rg10_evaluated.store, "SELECT hidden_suite_result, project_suite_result, "
                "suite_result_source FROM output WHERE gen_id = ?", gen_id) == [
                    ("fail", "fail", "rule")]


def test_all_symbol_counts_are_stored(rg10_evaluated: Rg10Evaluation) -> None:
    """Every output's symbol counts equal its detections on added lines, by symbol."""
    for gen_id, in rows(rg10_evaluated.store, "SELECT gen_id FROM output"):
        added = Counter(symbol for symbol, in rows(
            rg10_evaluated.store, "SELECT symbol FROM detection WHERE gen_id = ? AND "
            "on_added_line = 1", gen_id))
        stored = dict(rows(rg10_evaluated.store, "SELECT symbol, count FROM symbol_count "
                           "WHERE gen_id = ?", gen_id))
        assert stored == dict(added), gen_id
    assert rows(rg10_evaluated.store, "SELECT count(*) FROM symbol_count") != [(0,)]


def test_every_rate_is_count_over_declared(rg10_evaluated: Rg10Evaluation) -> None:
    """Every rate is count / declared; rates exist only for insertable, evaluable outputs."""
    for count, declared, rate, insertable, evaluability in rows(
            rg10_evaluated.store, "SELECT t.count, t.declared_opportunities, t.rate, "
            "o.insertable, o.evaluability FROM type_count t JOIN output o ON "
            "o.bundle_id = t.bundle_id AND o.gen_id = t.gen_id WHERE t.rate IS NOT NULL"):
        assert insertable == 1 and evaluability == "ok"
        assert rate == count / declared
    reasons = {reason for reason, in rows(rg10_evaluated.store, "SELECT DISTINCT "
                                           "exclusion_reason FROM type_count")}
    assert reasons == {None, "no declared opportunities", "not insertable", "SHA mismatch",
                       "no modified module", "lint failure"}
    assert rows(rg10_evaluated.store, "SELECT count(*) FROM type_count") == [(40,)]


def test_lint_in_tree_enables_the_mapping_rules(tmp_path: Path) -> None:
    """A rule the profile disables (C0116) is reported once the mapping enables it."""
    (tmp_path / "pkg").mkdir()
    profile = ResearchProfile()
    plain = profile.lint_in_tree(tmp_path, "pkg/mod.py", DOCSTRINGLESS)
    enabled = profile.lint_in_tree(tmp_path, "pkg/mod.py", DOCSTRINGLESS, ("C0116",))
    assert plain.status == enabled.status == OK
    assert "C0116" not in {message.message_id for message in plain.messages}
    assert "C0116" in {message.message_id for message in enabled.messages}
    assert not (tmp_path / "pkg" / "mod.py").exists()  # the tree itself is never written


def test_lint_in_tree_ignores_the_variants_pylint_files(fx_rg10_v2: FxRg10, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """The init-hook in pylintrc, .pylintrc and pyproject.toml never runs (NFR-26)."""
    hook = tmp_path / "hook.txt"
    monkeypatch.setenv(make_fx_rg10_v2.HOOK_VARIABLE, str(hook))
    monkeypatch.chdir(tmp_path)
    variant = fx_rg10_v2.path / "variants" / "T1" / "injected"
    result = ResearchProfile().lint_in_tree(variant, "shop/orders.py",
                                            (variant / "shop" / "orders.py").read_bytes())
    assert result.status == OK
    assert not hook.exists() and not (tmp_path / "fx_rg10_hook.txt").exists()


@pytest.mark.parametrize("target", ["/abs/mod.py", "../mod.py", "pkg/../../mod.py",
                                    "-mod.py", ""])
def test_lint_in_tree_needs_a_relative_target(tmp_path: Path, target: str) -> None:
    """The target is a relative POSIX path inside the tree, never an option or escape."""
    with pytest.raises(ValueError):
        ResearchProfile().lint_in_tree(tmp_path, target, DOCSTRINGLESS)
