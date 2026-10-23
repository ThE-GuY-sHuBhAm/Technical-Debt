"""Rg10Job: one RG10 bundle evaluation, committed as one unit (FR-58 to FR-61, NFR-25).

Freeze section 9, TDEA steps 6 to 13, in this order: the bundle is validated (FR-58,
BundleInvalid exit 1, done by ResearchService before the run identifier), every output is
rebuilt with prepare_module, checked statically and matched with its execution record
(FR-59, FR-60, evaluate_bundle), its modified module is linted inside its variant copy and
its smells counted (FR-61, SmellCounter), and then the whole bundle is written in one
transaction: run, bundle, the variants the manifest uses, tasks, declared opportunities,
outputs, execution records, test results, detections, symbol counts and type counts.
TDEA never imports or runs bundle code (NFR-26): the harness's records are only read.

Rules and readings (sprint 23 Oct):

* The bundle is one unit (NFR-25), so a killed evaluation leaves no record and starting
  the same run again redoes it whole. Starting a completed run again does nothing and
  exits 0 (FR-48, "Starting a completed run again shall do nothing and exit 0").
* A run with any unevaluable output (missing output, missing execution, SHA or Python
  mismatch, inconsistent record) ends "complete with exclusions": status
  complete_with_exclusions and exit 2 (FR-59). Missing outputs count as exclusions
  (CL-20, provisional reading).
* An execution record is stored only when it carries every typed field the execution
  entity requires; a record too malformed to store is still what made its output
  "inconsistent record", which the output row states.
* Test results are stored for the outputs whose suites the harness decided (source
  harness); a non-insertable output fails both suites with source "rule" and stores no
  test result (FR-60, as evaluate_bundle decides).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tdea.aggregator.research_models import (Bundle, DeclaredOpportunity, Detection, Execution,
                                             Output, Run, SymbolCount, Task, TestResult,
                                             TypeCount, Variant, canonical_json, iso_utc)
from tdea.aggregator.research_store import ResearchStore
from tdea.analyzer.research.rg10.bundle import LoadedBundle, OutputSpec
from tdea.analyzer.research.rg10.counts import OutputCounts, SmellCounter
from tdea.analyzer.research.rg10.execution import OK, OutputEvaluation, evaluate_bundle
from tdea.analyzer.research.rg10.static_check import StaticChecker

COMPLETE = "complete"
COMPLETE_WITH_EXCLUSIONS = "complete_with_exclusions"
FINISHED = (COMPLETE, COMPLETE_WITH_EXCLUSIONS)
_RECORD_TEXT = ("module_sha256", "tree_sha256", "python_version", "import_status",
                "prepare_module_version")


@dataclass(frozen=True)
class Rg10Context:
    """What one RG10 run needs: identifiers, metadata and the loaded bundle."""

    run_id: str
    canonical_run_file: str
    tdea_version: str
    key_fingerprint: str
    metadata: dict[str, object]
    bundle: LoadedBundle
    content_hash: str


@dataclass(frozen=True)
class Rg10Result:
    """What one start of an RG10 run did."""

    run_id: str
    outputs: int
    unevaluable: int
    already_complete: bool

    @property
    def with_exclusions(self) -> bool:
        """Return True when the run left outputs out (exit 2)."""
        return self.unevaluable > 0


class Rg10Job:
    """Evaluates one bundle and commits it as one unit."""

    def __init__(self, store: ResearchStore, counter: SmellCounter,
                 clock: Callable[[], datetime],
                 checker: StaticChecker | None = None) -> None:
        """Write to ``store``, count with ``counter`` and take times from ``clock``."""
        self._store = store
        self._counter = counter
        self._clock = clock
        self._checker = checker

    def run(self, context: Rg10Context) -> Rg10Result:
        """Evaluate and store the bundle, or do nothing for a completed run."""
        with self._store.reader() as session:
            stored = session.get(Run, context.run_id)
            if stored is not None:  # written with its bundle in one transaction: finished
                unevaluable = len(session.scalars(
                    select(Output.gen_id).join(Bundle, Output.bundle_id == Bundle.bundle_id)
                    .where(Bundle.run_id == context.run_id, Output.evaluability != OK)).all())
                total = len(session.scalars(
                    select(Output.gen_id).join(Bundle, Output.bundle_id == Bundle.bundle_id)
                    .where(Bundle.run_id == context.run_id)).all())
                return Rg10Result(context.run_id, total, unevaluable, True)
        started = iso_utc(self._clock())
        evaluations = evaluate_bundle(context.bundle, self._checker)
        counts = [self._counter.count(context.bundle, output, evaluation)
                  for output, evaluation in zip(context.bundle.outputs, evaluations)]
        unevaluable = sum(1 for evaluation in evaluations if evaluation.match.evaluability != OK)
        with self._store.transaction() as session:
            _write(session, context, evaluations, counts, started, iso_utc(self._clock()),
                   COMPLETE_WITH_EXCLUSIONS if unevaluable else COMPLETE)
        return Rg10Result(context.run_id, len(evaluations), unevaluable, False)


def _write(session: Session, context: Rg10Context, evaluations: tuple[OutputEvaluation, ...],
           counts: list[OutputCounts], started: str, ended: str, status: str) -> None:
    """Add every record of the bundle evaluation to ``session``."""
    metadata, bundle = context.metadata, context.bundle
    session.add(Run(
        run_id=context.run_id, kind="rg10", status=status, tdea_version=context.tdea_version,
        python_version=metadata["python_version"], pylint_version=metadata["pylint_version"],
        astroid_version=metadata["astroid_version"], radon_version=metadata["radon_version"],
        git_version=metadata.get("git_version"), gitpython_version=metadata.get(
            "gitpython_version"), profile_hash=metadata["profile_hash"],
        profile=canonical_json(metadata["profile"]),
        canonical_run_file=context.canonical_run_file,
        time_limit_seconds=metadata["time_limit_seconds"],
        key_fingerprint=context.key_fingerprint,
        prepare_module_version=metadata["prepare_module_version"], started_at=started,
        ended_at=ended))
    session.flush()
    row = Bundle(run_id=context.run_id, path=str(bundle.root),
                 schema_version=bundle.meta.schema_version,
                 harness_version=bundle.meta.harness_version,
                 prepare_module_version=bundle.meta.prepare_module_version,
                 env_fingerprint=bundle.meta.env_fingerprint,
                 smell_mapping=canonical_json({smell: list(rules)
                                               for smell, rules in bundle.smell_mapping}),
                 content_hash=context.content_hash)
    session.add(row)
    session.flush()
    variant_ids = {}
    for key in sorted(bundle.variant_projects, key=lambda key: (key[0], key[1], key[2] or "")):
        variant = bundle.variants[key]
        stored = Variant(bundle_id=row.bundle_id, smell=variant.smell,
                         condition=variant.condition, dose=variant.dose,
                         project=bundle.variant_projects[key],
                         file_hashes=canonical_json([list(pair) for pair in variant.file_hashes]))
        session.add(stored)
        session.flush()
        variant_ids[key] = stored.variant_id
    tasks = sorted(bundle.tasks.values(), key=lambda task: task.task_id)
    session.add_all(Task(bundle_id=row.bundle_id, task_id=task.task_id, project=task.project,
                         target_module=task.target_module, marker=task.marker,
                         function_names=canonical_json(list(task.function_names)),
                         hidden_test_ids=canonical_json(list(task.hidden_tests)),
                         project_test_ids=canonical_json(list(task.project_tests)))
                    for task in tasks)
    session.flush()  # the models declare no relationships, so parents are flushed first
    session.add_all(DeclaredOpportunity(bundle_id=row.bundle_id, task_id=task.task_id,
                                        smell_type=smell, declared_count=count)
                    for task in tasks for smell, count in task.declared_opportunities)
    session.flush()
    for output, evaluation, counted in zip(bundle.outputs, evaluations, counts):
        _write_output(session, row.bundle_id, variant_ids[output.variant_key], output,
                      evaluation, counted, bundle.records.get(output.gen_id))


def _write_output(session: Session, bundle_id: int, variant_id: int, output: OutputSpec,
                  evaluation: OutputEvaluation, counted: OutputCounts,
                  record: dict[str, object] | None) -> None:
    """Add one output and everything recorded for it."""
    match = evaluation.match
    first, last = evaluation.inserted_range or (None, None)
    session.add(Output(
        bundle_id=bundle_id, gen_id=output.gen_id, task_id=output.task_id,
        variant_id=variant_id, replicate=output.replicate, run_order=output.run_order,
        prompt_file=output.prompt_file, output_file=output.output_file,
        generation_metadata=output.generation_metadata,
        extraction_status=evaluation.extraction_status, inserted_first_line=first,
        inserted_last_line=last, n_added_lines=evaluation.n_added_lines,
        parses=evaluation.parses, parse_error=evaluation.parse_error,
        new_imports=_listed(evaluation.new_imports),
        redefinitions=_listed(evaluation.redefinitions),
        module_sha256=evaluation.module_sha256, tree_sha256=evaluation.tree_sha256,
        import_status=match.import_status, insertable=match.insertable,
        evaluability=match.evaluability, hidden_suite_result=match.hidden_suite_result,
        project_suite_result=match.project_suite_result,
        suite_result_source=match.suite_result_source))
    session.flush()
    if record is not None and _storable(record):
        session.add(Execution(
            bundle_id=bundle_id, gen_id=output.gen_id,
            module_sha256=record["module_sha256"], tree_sha256=record["tree_sha256"],
            python_version=record["python_version"], import_status=record["import_status"],
            import_error=record.get("import_error") if isinstance(
                record.get("import_error"), str) else None,
            executed_at=_text(record.get("executed_at")),
            harness_version=_text(record.get("harness_version")),
            prepare_module_version=record["prepare_module_version"]))
    session.add_all(TestResult(bundle_id=bundle_id, gen_id=output.gen_id, suite=test.suite,
                               test_id=test.test_id, outcome=test.outcome,
                               duration_s=test.duration_s, source=test.source)
                    for test in match.tests)
    session.add_all(Detection(bundle_id=bundle_id, gen_id=output.gen_id, symbol=found.symbol,
                              message_id=found.message_id, line=found.line,
                              message=found.message, on_added_line=found.on_added_line)
                    for found in counted.detections)
    session.add_all(SymbolCount(bundle_id=bundle_id, gen_id=output.gen_id, symbol=symbol,
                                count=count) for symbol, count in counted.symbol_counts)
    session.add_all(TypeCount(bundle_id=bundle_id, gen_id=output.gen_id,
                              smell_type=row.smell_type, count=row.count,
                              declared_opportunities=row.declared_opportunities,
                              is_target=row.is_target, rate=row.rate,
                              exclusion_reason=row.exclusion_reason)
                    for row in counted.type_counts)


def _storable(record: dict[str, object]) -> bool:
    """Return True when the record has every text field the execution entity requires."""
    return all(isinstance(record.get(name), str) for name in _RECORD_TEXT)


def _text(value: object) -> str | None:
    """Return a string field, or None."""
    return value if isinstance(value, str) else None


def _listed(values: tuple[str, ...] | None) -> str | None:
    """Return a name list as canonical JSON, or None."""
    return None if values is None else canonical_json(list(values))
