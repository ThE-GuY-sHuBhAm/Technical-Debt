"""ExecutionResultMatcher and test outcomes (FR-59, FR-60, D-17, D-18), component level.

The matcher decides, for a module that parses, from its execution record alone: the
evaluability value (the first that applies in FR-59's order), insertability, the suite
results and the recorded test outcomes. The readings marked [R] in execution.py are
pinned here as provisional behaviour. The FX-RG10 tests check that recorded outcomes
equal the fixture's records (FR-60). TDEA runs no test; records are data.
"""

from __future__ import annotations

from pathlib import Path

import make_fx_rg10
import pytest
from make_fx_rg10 import GEN_IDS

from tdea.analyzer.research.rg10.bundle import BundleLoader, TaskSpec
from tdea.analyzer.research.rg10.execution import (EVALUABILITY, INCONSISTENT_RECORD,
                                                   MISSING_EXECUTION, OK, PYTHON_MISMATCH,
                                                   SHA_MISMATCH, ExecutionResultMatcher, Match,
                                                   RecordedOutcome, evaluate_bundle,
                                                   failed_by_rule, unevaluable)
from tdea.analyzer.research.rg10.prepare import PREPARE_MODULE_VERSION

MODULE = "a" * 64
TREE = "b" * 64
DROP = object()
TASK = TaskSpec(1, "task-1", "shop", "shop/orders.py", "# T", ("discounted",), ("h1", "h2"),
                ("p1",), (("T1", 2), ("T2", 1)), Path("tests/task-1"))
TESTS = [{"suite": "project", "test_id": "p1", "outcome": "pass", "duration_s": None},
         {"suite": "hidden", "test_id": "h2", "outcome": "pass", "duration_s": 1},
         {"suite": "hidden", "test_id": "h1", "outcome": "pass", "duration_s": 0.5}]


def record(**changes: object) -> dict[str, object]:
    """Return a valid record for TASK, with fields changed or dropped (DROP)."""
    found: dict[str, object] = {
        "gen_id": "g01", "module_sha256": MODULE, "tree_sha256": TREE, "python_version": "3.11.9",
        "prepare_module_version": PREPARE_MODULE_VERSION, "import_status": "ok",
        "import_error": None, "tests": [dict(test) for test in TESTS],
        "env_fingerprint": {"network": "none"}, "executed_at": "2026-11-06T08:00:00Z",
        "harness_version": "h", "tdea_version": "0.1.0"}
    for key, value in changes.items():
        if value is DROP:
            del found[key]
        else:
            found[key] = value
    return found


def entry(**changes: object) -> dict[str, object]:
    """Return a valid hidden test entry with fields changed or dropped (DROP)."""
    return {key: value for key, value in {"suite": "hidden", "test_id": "h1", "outcome": "pass",
                                          "duration_s": 0.1, **changes}.items()
            if value is not DROP}


def decide(found: dict[str, object] | None, task: TaskSpec = TASK) -> Match:
    """Return the matcher's decision for ``found`` against MODULE and TREE."""
    return ExecutionResultMatcher().match(task, MODULE, TREE, found)


def test_a_valid_record_gives_an_insertable_output_with_its_outcomes() -> None:
    """All tests passed: insertable, evaluable, both suites pass, outcomes from the harness."""
    assert decide(record()) == Match(
        OK, True, "ok", "pass", "pass", "harness",
        (RecordedOutcome("hidden", "h1", "pass", 0.5), RecordedOutcome("hidden", "h2", "pass", 1.0),
         RecordedOutcome("project", "p1", "pass", None)), True)


def test_the_evaluability_values_are_fr59s() -> None:
    """The six evaluability values of FR-59's Outputs, in FR-59's order."""
    assert EVALUABILITY == ("ok", "missing output", "missing execution", "SHA mismatch",
                            "Python mismatch", "inconsistent record")


def test_no_record_is_missing_execution() -> None:
    """A parsing module without a record is unevaluable (FR-59)."""
    assert decide(None) == unevaluable(MISSING_EXECUTION)
    assert unevaluable(MISSING_EXECUTION) == Match(MISSING_EXECUTION, None, None, None, None,
                                                   None, (), None)


@pytest.mark.parametrize("changes", [
    {"module_sha256": "c" * 64}, {"tree_sha256": "c" * 64}, {"module_sha256": DROP},
    {"tree_sha256": None}, {"module_sha256": MODULE.upper()},
], ids=["module", "tree", "no module hash", "null tree hash", "upper-case hex"])
def test_hashes_must_equal_tdeas(changes: dict[str, object]) -> None:
    """Both SHA-256 values must equal TDEA's rebuild exactly (FR-59)."""
    assert decide(record(**changes)) == unevaluable(SHA_MISMATCH)


@pytest.mark.parametrize("version", ["3.12.4", "3.11", "3.110.1", 3.11, DROP, "Python 3.11.9"])
def test_the_record_must_come_from_python_3_11(version: object) -> None:
    """python_version must start with "3.11." (Freeze section 9)."""
    assert decide(record(python_version=version)) == unevaluable(PYTHON_MISMATCH)


def test_any_3_11_release_is_accepted() -> None:
    """A pre-release or any micro version of 3.11 starts with "3.11."."""
    assert decide(record(python_version="3.11.0rc1")).evaluability == OK


def test_the_first_applicable_value_wins() -> None:
    """[R] FR-59's order decides: SHA mismatch, then Python mismatch, then inconsistent."""
    assert decide(record(tree_sha256="c" * 64, python_version="3.12.4")).evaluability == (
        SHA_MISMATCH)
    assert decide(record(python_version="3.12.4", import_status="skipped")).evaluability == (
        PYTHON_MISMATCH)


@pytest.mark.parametrize("changes", [
    {"prepare_module_version": "2"}, {"prepare_module_version": DROP},
    {"import_status": "not_attempted"}, {"import_status": "skipped"}, {"import_status": DROP},
    {"import_error": DROP}, {"import_error": 5},
    {"tests": DROP}, {"tests": None}, {"tests": "h1 passed"}, {"tests": [1]},
    {"tests": [entry(duration_s=DROP)]}, {"tests": [entry(suite="unit")]},
    {"tests": [entry(outcome="skipped")]}, {"tests": [entry(test_id="")]},
    {"tests": [entry(test_id=5)]}, {"tests": [entry(duration_s=-1)]},
    {"tests": [entry(duration_s=True)]}, {"tests": [entry(duration_s="1")]},
    {"tests": [entry(suite=DROP)]},
    {"import_status": "error", "import_error": "ImportError: x", "tests": [entry()]},
], ids=lambda changes: ",".join(f"{key}={'DROP' if value is DROP else value!r}"[:40]
                                for key, value in changes.items()))
def test_inconsistent_records(changes: dict[str, object]) -> None:
    """[R] A record breaking any other rule of Freeze section 9 is "inconsistent record"."""
    assert decide(record(**changes)) == unevaluable(INCONSISTENT_RECORD)


@pytest.mark.parametrize("tests", [DROP, None, []], ids=["absent", "null", "empty"])
def test_an_import_error_is_non_insertable_and_fails_both_suites_by_rule(tests: object) -> None:
    """import_status error: non-insertable, evaluable, both suites fail by rule (FR-59, FR-60)."""
    assert decide(record(import_status="error", tests=tests,
                         import_error="ModuleNotFoundError: No module named 'x'")) == Match(
        OK, False, "error", "fail", "fail", "rule", (), None)
    assert failed_by_rule("error") == decide(record(import_status="error", tests=tests,
                                                    import_error=None))


@pytest.mark.parametrize("tests", [
    TESTS[:2], TESTS + [entry(test_id="h3")], TESTS + [entry(test_id="h1")],
    [TESTS[0], TESTS[1], entry(suite="project", test_id="h1")],
    [dict(TESTS[0], suite="hidden"), TESTS[1], TESTS[2]],
], ids=["a hidden test missing", "an extra test", "a test twice", "a test in the wrong suite",
        "a project test filed as hidden"])
def test_a_test_id_mismatch_leaves_only_the_outcomes_unevaluable(tests: list[object]) -> None:
    """D-18: the output stays insertable and evaluable; suite results are null; no outcome."""
    assert decide(record(tests=tests)) == Match(OK, True, "ok", None, None, None, (), False)


@pytest.mark.parametrize("outcome", ["fail", "error", "timeout"])
def test_a_suite_passes_only_if_every_test_passed(outcome: str) -> None:
    """FR-60: one test not passing fails its suite; the other suite is unaffected."""
    tests = [dict(TESTS[0]), dict(TESTS[1], outcome=outcome), dict(TESTS[2])]
    match = decide(record(tests=tests))
    assert (match.hidden_suite_result, match.project_suite_result) == ("fail", "pass")
    assert RecordedOutcome("hidden", "h2", outcome, 1.0) in match.tests


def test_a_suite_with_no_declared_tests_passes() -> None:
    """[R] A task that declares no project tests has a passing project suite."""
    task = TaskSpec(1, "t", "p", "m.py", "# T", (), ("h1",), (), (("T1", 1),), Path("t"))
    match = decide(record(tests=[entry()]), task)
    assert (match.hidden_suite_result, match.project_suite_result, match.tests_evaluable) == (
        "pass", "pass", True)


def test_outcomes_are_ordered_and_other_keys_are_not_read() -> None:
    """[R] Outcomes are sorted by suite and test ID; extra keys change nothing."""
    tests = [dict(entry, stdout="ignored") for entry in reversed(TESTS)]
    assert decide(record(tests=tests, extra_key=1, executed_at=DROP, harness_version=DROP,
                         tdea_version=DROP)) == decide(record())


def test_fx_rg10_outcomes_equal_its_records(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """FR-60: for each insertable output with matching IDs, outcomes equal the record's tests."""
    loaded = BundleLoader().load(fx_rg10.path)
    found = {evaluation.gen_id: evaluation for evaluation in evaluate_bundle(loaded)}
    harness = [gen_id for gen_id, evaluation in found.items()
               if evaluation.match.suite_result_source == "harness"]
    assert harness == [GEN_IDS[label] for label in ("one_python", "py_then_python3",
                                                    "class_body", "new_import", "redefinition",
                                                    "clean", "sentinel")]
    for gen_id in harness:
        expected = sorted(((item["suite"], item["test_id"], item["outcome"],
                            None if item["duration_s"] is None else float(item["duration_s"]))
                           for item in loaded.records[gen_id]["tests"]))
        assert [(outcome.suite, outcome.test_id, outcome.outcome, outcome.duration_s)
                for outcome in found[gen_id].match.tests] == expected
        assert {outcome.source for outcome in found[gen_id].match.tests} == {"harness"}


@pytest.mark.parametrize("label, hidden, project", [
    ("one_python", "pass", "pass"), ("py_then_python3", "fail", "pass"),
    ("class_body", "fail", "fail"), ("new_import", "fail", "pass"),
])
def test_fx_rg10_suite_results(fx_rg10: make_fx_rg10.FxRg10, label: str, hidden: str,
                               project: str) -> None:
    """Suite results follow FR-60 from FX-RG10's recorded pass, fail, error and timeout."""
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(fx_rg10.path))}
    match = found[GEN_IDS[label]].match
    assert (match.hidden_suite_result, match.project_suite_result) == (hidden, project)


def test_fx_rg10_record_missing_a_hidden_test(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """D-18 in FX-RG10: the output stays insertable and evaluable with null suite results."""
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(fx_rg10.path))}
    assert found[GEN_IDS["missing_hidden_test"]].match == Match(OK, True, "ok", None, None,
                                                                None, (), False)
