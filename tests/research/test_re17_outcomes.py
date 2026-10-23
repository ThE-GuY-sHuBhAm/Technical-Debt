"""RE-17: non-execution and test-outcome recording on FX-RG10 v2 (FR-60, NFR-26; sprint 23 Oct).

The bundle is evaluated once through ``tdea research run`` (rg10_run.evaluate), with the
hostile outputs' targets set and a local listener waiting. Checked here: no sentinel,
spawn or init-hook file and no connection; the evaluation finishes within the
per-module Pylint limit; the module nested 10,000 levels deep is recorded as not parsing
and the run completes; recorded outcomes (pass, fail, error, timeout) equal the fixture's
execution records; non-insertable outputs fail both suites with source "rule"; the record
missing a hidden test keeps its output in the rates with null suite results.

Scope of this evidence: FX-RG10 v2 is SYNTHETIC, so this is mechanics evidence on Linux
with Python 3.11 only. RE-17 step 1 also asks for macOS and Windows, which the CI matrix
covers and this sprint did not run.
"""

from __future__ import annotations

import json

import make_fx_rg10_v2
from make_fx_rg10 import FxRg10, read_jsonl
from rg10_run import Rg10Evaluation
from run_support import rows

from tdea.analyzer.research.profile import TIME_LIMIT_SECONDS
from tdea.api.cli import EXIT_OK


def test_hostile_outputs_write_nothing_and_connect_nowhere(
        rg10_evaluated: Rg10Evaluation) -> None:
    """Steps 1-2: no sentinel, spawn or init-hook file, no connection, within the limits."""
    assert rg10_evaluated.written == ()
    assert rg10_evaluated.listener_connected is False
    assert rg10_evaluated.seconds < TIME_LIMIT_SECONDS


def test_the_init_hook_files_are_in_the_variant(fx_rg10_v2: FxRg10) -> None:
    """The init-hook check is not vacuous: the linted variant carries all three files."""
    variant = fx_rg10_v2.path / "variants" / "T1" / "injected"
    for name in make_fx_rg10_v2.INIT_HOOK_FILES:
        assert make_fx_rg10_v2.HOOK_VARIABLE in (variant / name).read_text(encoding="utf-8")


def test_the_nested_module_is_recorded_as_not_parsing(rg10_evaluated: Rg10Evaluation,
                                                       fx_rg10_v2: FxRg10) -> None:
    """The module 10,000 levels deep does not parse, and every output is still stored."""
    nested = fx_rg10_v2.gen_ids["nested"]
    assert rows(rg10_evaluated.store, "SELECT parses, import_status, insertable FROM output "
                "WHERE gen_id = ?", nested) == [(0, "not_attempted", 0)]
    assert rows(rg10_evaluated.store, "SELECT count(*) FROM output") == [(20,)]
    assert rows(rg10_evaluated.store, "SELECT kind, status FROM run") == [
        ("rg10", "complete_with_exclusions")]


def test_recorded_outcomes_equal_the_execution_records(rg10_evaluated: Rg10Evaluation,
                                                       fx_rg10_v2: FxRg10) -> None:
    """Step 3: each insertable, evaluable output's stored outcomes are its record's tests."""
    records = {record["gen_id"]: record
               for record in read_jsonl(fx_rg10_v2.path / "execution.jsonl")}
    compared = 0
    for gen_id, in rows(rg10_evaluated.store, "SELECT gen_id FROM output WHERE insertable = 1 "
                        "AND evaluability = 'ok' AND suite_result_source = 'harness'"):
        expected = sorted((test["suite"], test["test_id"], test["outcome"],
                           None if test["duration_s"] is None else float(test["duration_s"]),
                           "harness") for test in records[gen_id]["tests"])
        assert rows(rg10_evaluated.store, "SELECT suite, test_id, outcome, duration_s, source "
                    "FROM test_result WHERE gen_id = ? ORDER BY suite, test_id, outcome",
                    gen_id) == expected, gen_id
        compared += 1
    assert compared == 9
    outcomes = {outcome for outcome, in rows(rg10_evaluated.store,
                                              "SELECT DISTINCT outcome FROM test_result")}
    assert outcomes == {"pass", "fail", "error", "timeout"}


def test_a_suite_passes_only_when_every_test_passed(rg10_evaluated: Rg10Evaluation) -> None:
    """FR-60: a harness suite result is pass exactly when all its stored tests passed."""
    for gen_id, hidden, project in rows(
            rg10_evaluated.store, "SELECT gen_id, hidden_suite_result, project_suite_result "
            "FROM output WHERE suite_result_source = 'harness'"):
        for suite, result in (("hidden", hidden), ("project", project)):
            outcomes = [outcome for outcome, in rows(
                rg10_evaluated.store, "SELECT outcome FROM test_result WHERE gen_id = ? "
                "AND suite = ?", gen_id, suite)]
            assert result == ("pass" if set(outcomes) == {"pass"} else "fail"), (gen_id, suite)


def test_non_insertable_outputs_fail_both_suites_by_rule(rg10_evaluated: Rg10Evaluation,
                                                         fx_rg10_v2: FxRg10) -> None:
    """Every non-insertable output fails both suites with source "rule" and no test rows."""
    found = rows(rg10_evaluated.store, "SELECT gen_id, hidden_suite_result, "
                 "project_suite_result, suite_result_source FROM output WHERE insertable = 0 "
                 "ORDER BY gen_id")
    labels = {gen_id: label for label, gen_id in fx_rg10_v2.gen_ids.items()}
    assert sorted(labels[gen_id] for gen_id, *_ in found) == sorted(
        ["no_block", "unterminated", "only_js", "syntax_error", "import_missing", "nested",
         "listener", "loop"])
    assert {tuple(row[1:]) for row in found} == {("fail", "fail", "rule")}
    assert rows(rg10_evaluated.store, "SELECT count(*) FROM test_result t JOIN output o ON "
                "o.bundle_id = t.bundle_id AND o.gen_id = t.gen_id WHERE o.insertable = 0") == [
                    (0,)]


def test_the_incomplete_record_stays_in_the_rates_with_null_suites(
        rg10_evaluated: Rg10Evaluation, fx_rg10_v2: FxRg10) -> None:
    """D-18: a record missing one hidden test gives null suite results but keeps rates."""
    gen_id = fx_rg10_v2.gen_ids["missing_hidden_test"]
    assert rows(rg10_evaluated.store, "SELECT evaluability, insertable, hidden_suite_result, "
                "project_suite_result, suite_result_source FROM output WHERE gen_id = ?",
                gen_id) == [("ok", 1, None, None, None)]
    assert rows(rg10_evaluated.store, "SELECT smell_type, rate, exclusion_reason FROM "
                "type_count WHERE gen_id = ? ORDER BY smell_type", gen_id) == [
                    ("T1", 0.0, None), ("T2", 0.0, None)]


def test_exclusions_exit_2_and_a_restart_does_nothing(rg10_evaluated: Rg10Evaluation) -> None:
    """Two SHA mismatches end the run with exclusions (exit 2); a restart exits 0 unchanged."""
    assert rg10_evaluated.exit_code == 2
    assert rg10_evaluated.restart_exit_code == EXIT_OK
    assert rg10_evaluated.after_restart == rg10_evaluated.before_restart
    assert rows(rg10_evaluated.store, "SELECT evaluability, count(*) FROM output "
                "GROUP BY evaluability ORDER BY evaluability") == [("SHA mismatch", 2),
                                                                     ("ok", 18)]


def test_the_bundle_row_keeps_its_content_hash_and_mapping(
        rg10_evaluated: Rg10Evaluation) -> None:
    """The run identifier's bundle content hash and the smell mapping are stored."""
    ((content_hash, mapping),) = rows(rg10_evaluated.store,
                                      "SELECT content_hash, smell_mapping FROM bundle")
    assert len(content_hash) == 64
    assert json.loads(mapping) == {"T1": ["W0718", "W0702"], "T2": ["C0209"]}
