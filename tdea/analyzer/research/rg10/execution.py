"""ExecutionResultMatcher, test outcomes and evaluate_bundle (FR-59, FR-60; Task 8 Day 9).

TDEA never runs a test or imports a module (NFR-26, Freeze Decision 1): the research
harness executes each parsing module and writes an execution record, and TDEA checks
that record against the module it rebuilt itself. This module applies FR-59's
insertability rule and FR-60's outcome rules, in memory. It stores nothing and decides
no exit code: storing the records and ending the run "complete with exclusions" belong to
the RG10 unit, which waits for Day 9 decisions D9-1 and D9-4 (Option B).

evaluate_bundle() takes a LoadedBundle and returns one OutputEvaluation per output, in
gen_id order: prepare_module rebuilds the module (FR-59, D-16, D-17); StaticChecker parses
it and flags new imports and redefinitions; tree_sha256 hashes the executed tree; the
ExecutionResultMatcher reads the output's execution record.

Rules. [F] is frozen text; [R] a provisional reading needed to apply it.

* A missing output (FR-58) is unevaluable, "missing output"; nothing else is recorded.
* Extraction status none or unterminated: no module, non-insertable, both suites fail
  with source "rule", no record needed (D-17) [F]. A module that does not parse: the
  same (FR-59, FR-60) [F]. Such outputs are evaluable ("ok") [R], and a record the
  harness wrote for one anyway is not read [R].
* A parsing module (FR-59, Freeze section 9) [F] is unevaluable when its record is
  missing ("missing execution"), when its module_sha256 or tree_sha256 differs from
  TDEA's ("SHA mismatch"), when its python_version does not start with "3.11." ("Python
  mismatch"), or when it is otherwise inconsistent ("inconsistent record"). The first of
  these in FR-59's order of values applies [R]. Inconsistent [R]: prepare_module_version
  other than TDEA's; import_status other than ok or error (not_attempted is only for a
  module that does not parse [F]); import_error absent or neither a string nor null;
  with import_status ok, tests not a list of {suite, test_id, outcome, duration_s} with
  suite hidden or project, a non-empty test_id, an outcome of pass, fail, error or
  timeout and a duration that is null or a number >= 0; with import_status error, tests
  present and not empty. Other keys are not read; executed_at, harness_version and
  tdea_version are recorded by the harness and not checked here.
* Otherwise the output is evaluable. Insertable = import_status ok [F]. A non-insertable
  output fails both suites with source "rule" and records no test outcome [F].
* For an insertable output, the record's hidden and project test IDs must equal the
  task's declared IDs, each exactly once [F, D-18]. If they do not, only its test outcomes
  are unevaluable: no test outcome is recorded and both suite results are null, while the
  output stays insertable and evaluable, so it stays in the rates (tests_evaluable is
  False) [F]. If they do, every test outcome is recorded with source "harness", and a
  suite passes only if every one of its tests passed [F]; a suite with no declared tests
  passes [R].
* Test outcomes are ordered by suite, then test ID (their primary key) [R].
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tdea.analyzer.research.rg10.bundle import LoadedBundle, OutputSpec, TaskSpec
from tdea.analyzer.research.rg10.prepare import (PREPARE_MODULE_VERSION, prepare_module,
                                                 tree_sha256)
from tdea.analyzer.research.rg10.static_check import StaticChecker

OK = "ok"
MISSING_OUTPUT = "missing output"
MISSING_EXECUTION = "missing execution"
SHA_MISMATCH = "SHA mismatch"
PYTHON_MISMATCH = "Python mismatch"
INCONSISTENT_RECORD = "inconsistent record"
EVALUABILITY = (OK, MISSING_OUTPUT, MISSING_EXECUTION, SHA_MISMATCH, PYTHON_MISMATCH,
                INCONSISTENT_RECORD)
IMPORT_OK = "ok"
IMPORT_ERROR = "error"
SUITES = ("hidden", "project")
OUTCOMES = ("pass", "fail", "error", "timeout")
PASS = "pass"
FAIL = "fail"
HARNESS = "harness"
RULE = "rule"
PYTHON_PREFIX = "3.11."
TEST_FIELDS = ("suite", "test_id", "outcome", "duration_s")


@dataclass(frozen=True)
class RecordedOutcome:
    """One test outcome taken from an execution record (FR-60)."""

    suite: str
    test_id: str
    outcome: str
    duration_s: float | None
    source: str = HARNESS


@dataclass(frozen=True)
class Match:
    """What FR-59 and FR-60 decide for one output from its execution record."""

    evaluability: str
    insertable: bool | None
    import_status: str | None
    hidden_suite_result: str | None
    project_suite_result: str | None
    suite_result_source: str | None
    tests: tuple[RecordedOutcome, ...]
    tests_evaluable: bool | None


def unevaluable(reason: str) -> Match:
    """Return the decision for an output left out of rates and test outcomes."""
    return Match(reason, None, None, None, None, None, (), None)


def failed_by_rule(import_status: str | None = None) -> Match:
    """Return the decision for a non-insertable output: both suites fail by rule."""
    return Match(OK, False, import_status, FAIL, FAIL, RULE, (), None)


class ExecutionResultMatcher:
    """Matches a parsing module's execution record (FR-59) and reads its tests (FR-60)."""

    def match(self, task: TaskSpec, module_sha256: str, tree_digest: str,
              record: Mapping[str, object] | None) -> Match:
        """Return the decision for a module that parses, given its record or None."""
        if record is None:
            return unevaluable(MISSING_EXECUTION)
        if (record.get("module_sha256") != module_sha256
                or record.get("tree_sha256") != tree_digest):
            return unevaluable(SHA_MISMATCH)
        version = record.get("python_version")
        if not isinstance(version, str) or not version.startswith(PYTHON_PREFIX):
            return unevaluable(PYTHON_MISMATCH)
        tests = self.consistent_tests(record)
        if tests is None:
            return unevaluable(INCONSISTENT_RECORD)
        if record["import_status"] == IMPORT_ERROR:
            return failed_by_rule(IMPORT_ERROR)
        if not same_test_ids(tests, task):
            return Match(OK, True, IMPORT_OK, None, None, None, (), False)
        return Match(OK, True, IMPORT_OK, suite_result(tests, "hidden"),
                     suite_result(tests, "project"), HARNESS, tests, True)

    @staticmethod
    def consistent_tests(record: Mapping[str, object]) -> tuple[RecordedOutcome, ...] | None:
        """Return the record's test outcomes, or None if the record is inconsistent."""
        if record.get("prepare_module_version") != PREPARE_MODULE_VERSION:
            return None
        status = record.get("import_status")
        if status not in (IMPORT_OK, IMPORT_ERROR) or "import_error" not in record:
            return None
        if not (record["import_error"] is None or isinstance(record["import_error"], str)):
            return None
        tests = record.get("tests")
        if status == IMPORT_ERROR:
            return () if tests is None or tests == [] else None
        if not isinstance(tests, list):
            return None
        outcomes = [_outcome(entry) for entry in tests]
        if any(outcome is None for outcome in outcomes):
            return None
        return tuple(sorted((outcome for outcome in outcomes if outcome is not None),
                            key=lambda outcome: (outcome.suite, outcome.test_id)))


def _outcome(entry: object) -> RecordedOutcome | None:
    """Return one test entry as an outcome, or None if it is malformed."""
    if not isinstance(entry, dict) or any(field not in entry for field in TEST_FIELDS):
        return None
    suite, test_id, outcome, duration = (entry[field] for field in TEST_FIELDS)
    if (suite not in SUITES or outcome not in OUTCOMES or not isinstance(test_id, str)
            or not test_id):
        return None
    if duration is None:
        return RecordedOutcome(suite, test_id, outcome, None)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        return None
    return RecordedOutcome(suite, test_id, outcome, float(duration))


def same_test_ids(tests: tuple[RecordedOutcome, ...], task: TaskSpec) -> bool:
    """Return True if each suite lists exactly the task's declared test IDs, once each."""
    return all(sorted(outcome.test_id for outcome in tests if outcome.suite == suite)
               == sorted(declared)
               for suite, declared in (("hidden", task.hidden_tests),
                                       ("project", task.project_tests)))


def suite_result(tests: tuple[RecordedOutcome, ...], suite: str) -> str:
    """Return pass if every test of ``suite`` passed, else fail (FR-60)."""
    return PASS if all(outcome.outcome == PASS for outcome in tests
                       if outcome.suite == suite) else FAIL


@dataclass(frozen=True)
class OutputEvaluation:
    """One output's FR-59 and FR-60 results; match holds the record's decision."""

    gen_id: str
    extraction_status: str | None
    inserted_range: tuple[int, int] | None
    n_added_lines: int | None
    parses: bool | None
    parse_error: str | None
    new_imports: tuple[str, ...] | None
    redefinitions: tuple[str, ...] | None
    module_sha256: str | None
    tree_sha256: str | None
    match: Match


def evaluate_bundle(bundle: LoadedBundle,
                    checker: StaticChecker | None = None) -> tuple[OutputEvaluation, ...]:
    """Return every output's evaluation, in gen_id order, without storing anything."""
    checker = StaticChecker() if checker is None else checker
    matcher = ExecutionResultMatcher()
    return tuple(_evaluate(bundle, output, checker, matcher) for output in bundle.outputs)


def _evaluate(bundle: LoadedBundle, output: OutputSpec, checker: StaticChecker,
              matcher: ExecutionResultMatcher) -> OutputEvaluation:
    """Return one output's evaluation."""
    if output.text is None:
        return OutputEvaluation(output.gen_id, None, None, None, None, None, None, None, None,
                                None, unevaluable(MISSING_OUTPUT))
    task = bundle.tasks[output.task_id]
    variant = bundle.variants[output.variant_key]
    variant_text = bundle.modules[(variant.key, task.target_module)]
    prepared = prepare_module(variant_text, task.marker, output.text)
    if prepared.text is None:
        return OutputEvaluation(output.gen_id, prepared.status.value, None,
                                prepared.n_added_lines, None, None, None, None, None, None,
                                failed_by_rule())
    static = checker.check(variant_text, prepared)
    tree = tree_sha256(variant.root, task.target_module, prepared.text, task.hidden_tests_dir,
                       task.task_id)
    match = (matcher.match(task, static.module_sha256, tree, bundle.records.get(output.gen_id))
             if static.parses else failed_by_rule())
    return OutputEvaluation(output.gen_id, prepared.status.value, prepared.inserted_range,
                            prepared.n_added_lines, static.parses, static.parse_error,
                            static.new_imports, static.redefinitions, static.module_sha256,
                            tree, match)
