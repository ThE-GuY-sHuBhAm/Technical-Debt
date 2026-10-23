"""Record-level research export: what FR-62 exports, read from research.db.

This module selects and shapes the records; the api layer's ResearchExporter writes them
as CSV, JSON or JSONL files with metadata.json and data_dictionary.csv. It reads only.

Rules (Freeze FR-62, NFR-27, RM-07), and the readings taken here (sprint 23 Oct):

* One record file per entity of Freeze section 7 except the two lint-cache entities, so
  every export holds the same 21 files; a file for the other kind's entities has no rows.
* Every run must exist (else invalid input, exit 1), be complete (status complete or
  complete with exclusions; else refused, exit 3, a reading: the Freeze names no code for
  an unfinished run) and share one kind and one key fingerprint (else refused, exit 3).
* Record files hold identity keys only. CL-14 is open: FR-62 exports one file per entity
  and the commit entity holds four identity keys, but Decision 2 says name keys are never
  exported. Until the FCN, the stricter text applies: the two name-key columns of commit
  are left out, so commits carry author_email_key and committer_email_key only (the
  author_id and committer_id of Decision 2). PROVISIONAL_CL14 names this choice.
* Run, clone and completion times, local paths and the export time are left out of the
  record files and reported in metadata.json only (VOLATILE).
* Mode and local-fix fields carry unvalidated = true: item_mode stores the flag itself;
  unit_summary's local-fix fields get a derived ``unvalidated`` column, always true.
* RG1 records include the c1 SHA (FR-62): debt_item gets derived ``introducing_sha`` and
  ``c1_sha`` columns, from its two commit references.
* Rows are ordered by primary key (for the composite keys, by every key column in order).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import Table, select
from sqlalchemy.orm import Session

from tdea.aggregator.research_models import ResearchBase
from tdea.aggregator.research_store import ResearchStore

EXIT_INVALID_INPUT = 1
EXIT_REFUSED = 3
COMPLETE_STATUSES = ("complete", "complete_with_exclusions")

RG1_ENTITIES = ("run", "repository", "unit", "unit_summary", "commit", "file_change",
                "debt_item", "disappearance", "item_mode", "base_rate", "covariate")
RG10_ENTITIES = ("bundle", "variant", "task", "declared_opportunity", "output", "execution",
                 "test_result", "detection", "symbol_count", "type_count")
ENTITIES = RG1_ENTITIES + RG10_ENTITIES  # 21: Freeze section 7 without the lint cache

PROVISIONAL_CL14 = ("author_name_key", "committer_name_key")
VOLATILE = {
    "run": ("started_at", "ended_at"),
    "repository": ("clone_time",),
    "unit": ("completed_at",),
    "bundle": ("path",),
}
EXCLUDED = {**VOLATILE, "commit": PROVISIONAL_CL14}
EXPLORATORY = {("covariate", "account_tenure_days"), ("covariate", "prior_commits_to_file"),
               ("covariate", "later_activity")}
UNVALIDATED = {("item_mode", "mode"), ("item_mode", "local_fix"), ("item_mode", "unvalidated"),
               ("unit_summary", "local_fixes"), ("unit_summary", "local_fix_share"),
               ("unit_summary", "unvalidated")}


class ExportRefused(Exception):
    """An export that cannot be made: three FR-05 parts and its exit code."""

    def __init__(self, error_type: str, cause: str, action: str, exit_code: int) -> None:
        """Keep the three parts and the exit code."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action
        self.exit_code = exit_code


@dataclass(frozen=True)
class Column:
    """One exported column: its name, type, unit and description."""

    name: str
    type: str
    unit: str
    description: str


@dataclass
class RecordFile:
    """One entity's exported columns and rows (values are Python scalars or None)."""

    entity: str
    columns: tuple[Column, ...]
    rows: list[tuple[object, ...]] = field(default_factory=list)


@dataclass(frozen=True)
class ExportData:
    """Everything one export writes: the record files and the metadata's content."""

    kind: str
    key_fingerprint: str
    files: tuple[RecordFile, ...]
    runs: tuple[dict[str, object], ...]
    exclusions: tuple[dict[str, object], ...]


def collect(store: ResearchStore, run_ids: Sequence[str]) -> ExportData:
    """Return the records of ``run_ids`` in ``store``, or raise ExportRefused."""
    if not run_ids:
        raise ExportRefused("No run named", "The export needs at least one run identifier.",
                            "Name the runs to export; tdea research status lists them.",
                            EXIT_INVALID_INPUT)
    wanted = tuple(dict.fromkeys(run_ids))
    tables = ResearchBase.metadata.tables
    with store.reader() as session:
        runs = _check_runs(session, tables["run"], wanted)
        files = tuple(_record_file(session, tables, entity, wanted) for entity in ENTITIES)
        exclusions = tuple(_exclusions(session, tables, wanted))
        volatile = tuple(_volatile(session, tables, run) for run in runs)
    return ExportData(runs[0]["kind"], runs[0]["key_fingerprint"], files, volatile, exclusions)


def _check_runs(session: Session, run: Table, wanted: tuple[str, ...]) -> list[dict]:
    """Return the named runs as mappings; refuse unknown, unfinished or mixed runs."""
    found = {row["run_id"]: dict(row) for row in session.execute(
        select(run).where(run.c.run_id.in_(wanted))).mappings()}
    missing = [run_id for run_id in wanted if run_id not in found]
    if missing:
        raise ExportRefused("Unknown run", f"This research store has no run {', '.join(missing)}.",
                            "Check the identifiers with tdea research status.",
                            EXIT_INVALID_INPUT)
    unfinished = [run_id for run_id in wanted if found[run_id]["status"] not in COMPLETE_STATUSES]
    if unfinished:
        raise ExportRefused("Run not complete",
                            f"Run {', '.join(unfinished)} has not completed, so its records "
                            "are not final.",
                            "Run the same run file again until it completes, then export.",
                            EXIT_REFUSED)
    for attribute, what in (("kind", "kinds"), ("key_fingerprint", "key fingerprints")):
        values = sorted({str(found[run_id][attribute]) for run_id in wanted})
        if len(values) > 1:
            raise ExportRefused("Runs cannot be exported together",
                                f"The runs have different {what} ({', '.join(values)}); one "
                                "export holds runs of one kind and one key only (FR-62, "
                                "NFR-27).", "Export the runs separately.", EXIT_REFUSED)
    return [found[run_id] for run_id in wanted]


def _columns(table: Table, entity: str) -> list[Column]:
    """Return the stored columns of ``entity`` that the export keeps, described."""
    excluded = set(EXCLUDED.get(entity, ()))
    return [describe(entity, column.name, column.type.python_type)
            for column in table.columns if column.name not in excluded]


def _record_file(session: Session, tables, entity: str, wanted: tuple[str, ...]) -> RecordFile:
    """Return one entity's rows for the wanted runs, ordered by primary key."""
    table = tables[entity]
    columns = _columns(table, entity)
    query = select(*(table.c[column.name] for column in columns))
    query = _restrict(query, tables, entity, wanted).order_by(*table.primary_key.columns)
    rows = [tuple(row) for row in session.execute(query)]
    if entity == "unit_summary":
        columns.append(describe(entity, "unvalidated", bool))
        rows = [row + (True,) for row in rows]
    if entity == "debt_item":
        columns, rows = _with_shas(session, tables["commit"], columns, rows)
    return RecordFile(entity, tuple(columns), rows)


def _restrict(query, tables, entity: str, wanted: tuple[str, ...]):
    """Limit ``query`` to rows that reach one of the wanted runs through their parents."""
    run_id = tables["run"].c.run_id
    unit, repository, bundle = tables["unit"], tables["repository"], tables["bundle"]
    commit, item = tables["commit"], tables["debt_item"]
    table = tables[entity]
    if entity == "run":
        return query.where(run_id.in_(wanted))
    if entity in ("repository", "unit"):
        return query.where(table.c.run_id.in_(wanted))
    if entity == "bundle":
        return query.where(bundle.c.run_id.in_(wanted))
    if entity == "unit_summary":
        return query.join(unit, table.c.unit_id == unit.c.unit_id).where(unit.c.run_id.in_(wanted))
    if entity == "commit":
        return query.join(repository, table.c.repository_id == repository.c.repository_id) \
            .where(repository.c.run_id.in_(wanted))
    if entity == "file_change":
        return query.join(commit, table.c.commit_id == commit.c.commit_id) \
            .join(repository, commit.c.repository_id == repository.c.repository_id) \
            .where(repository.c.run_id.in_(wanted))
    if entity == "debt_item":
        return query.join(unit, table.c.unit_id == unit.c.unit_id).where(unit.c.run_id.in_(wanted))
    if entity in ("disappearance", "item_mode", "base_rate", "covariate"):
        return query.join(item, table.c.item_id == item.c.item_id) \
            .join(unit, item.c.unit_id == unit.c.unit_id).where(unit.c.run_id.in_(wanted))
    # Every RG10 entity carries bundle_id.
    return query.join(bundle, table.c.bundle_id == bundle.c.bundle_id) \
        .where(bundle.c.run_id.in_(wanted))


def _with_shas(session: Session, commit: Table, columns: list[Column],
               rows: list[tuple]) -> tuple[list[Column], list[tuple]]:
    """Add the introducing and c1 commit SHAs to debt_item rows (FR-62)."""
    names = [column.name for column in columns]
    introducing, c1 = names.index("introducing_commit_id"), names.index("c1_commit_id")
    ids = {row[introducing] for row in rows} | {row[c1] for row in rows if row[c1] is not None}
    shas = dict(session.execute(select(commit.c.commit_id, commit.c.sha)
                                .where(commit.c.commit_id.in_(ids))).tuples().all())
    columns = columns + [describe("debt_item", "introducing_sha", str),
                         describe("debt_item", "c1_sha", str)]
    return columns, [row + (shas[row[introducing]], shas.get(row[c1])) for row in rows]


def _exclusions(session: Session, tables, wanted: tuple[str, ...]) -> Iterable[dict]:
    """Yield what the runs left out: lint failures and late commits, unevaluable outputs."""
    unit, repository = tables["unit"], tables["repository"]
    for row in session.execute(
            select(unit.c.run_id, repository.c.url, unit.c.window_start, unit.c.window_end,
                   unit.c.lint_failures, unit.c.commits_after_cutoff)
            .join(repository, unit.c.repository_id == repository.c.repository_id)
            .where(unit.c.run_id.in_(wanted)).order_by(unit.c.unit_id)).mappings():
        if row["lint_failures"] or row["commits_after_cutoff"]:
            yield {"run_id": row["run_id"], "entity": "unit", "repository": row["url"],
                   "window_start": row["window_start"], "window_end": row["window_end"],
                   "lint_failures": row["lint_failures"],
                   "commits_after_cutoff": row["commits_after_cutoff"]}
    output, bundle = tables["output"], tables["bundle"]
    for row in session.execute(
            select(bundle.c.run_id, output.c.gen_id, output.c.evaluability,
                   output.c.insertable)
            .join(bundle, output.c.bundle_id == bundle.c.bundle_id)
            .where(bundle.c.run_id.in_(wanted))
            .order_by(output.c.bundle_id, output.c.gen_id)).mappings():
        if row["evaluability"] != "ok":
            yield {"run_id": row["run_id"], "entity": "output", "gen_id": row["gen_id"],
                   "reason": row["evaluability"]}


def _volatile(session: Session, tables, run: dict) -> dict[str, object]:
    """Return a run's metadata, with the fields record files leave out (FR-62)."""
    run_id = run["run_id"]
    repository, unit, bundle = tables["repository"], tables["unit"], tables["bundle"]
    summary = {key: _jsonable(key, value) for key, value in run.items()}
    summary["repositories"] = [
        dict(row) for row in session.execute(
            select(repository.c.repository_id, repository.c.url, repository.c.clone_time)
            .where(repository.c.run_id == run_id).order_by(repository.c.repository_id))
        .mappings()]
    summary["units"] = [
        dict(row) for row in session.execute(
            select(unit.c.unit_id, unit.c.completed_at).where(unit.c.run_id == run_id)
            .order_by(unit.c.unit_id)).mappings()]
    summary["bundles"] = [
        dict(row) for row in session.execute(
            select(bundle.c.bundle_id, bundle.c.path).where(bundle.c.run_id == run_id)
            .order_by(bundle.c.bundle_id)).mappings()]
    return summary


def _jsonable(key: str, value: object) -> object:
    """Return a run column for metadata.json: JSON text columns decoded, others as they are."""
    if key in ("profile", "canonical_run_file", "seeds", "rule_list_versions") and \
            isinstance(value, str):
        return json.loads(value)
    return value


def data_dictionary(files: Iterable[RecordFile]) -> list[tuple[str, str, str, str, str, bool,
                                                              bool]]:
    """Return data_dictionary.csv's rows: file, column, type, unit, description, flags."""
    return [(record.entity, column.name, column.type, column.unit, column.description,
             (record.entity, column.name) in UNVALIDATED,
             (record.entity, column.name) in EXPLORATORY)
            for record in files for column in record.columns]


_TYPES = {str: "text", int: "integer", float: "real", bool: "boolean"}
_JSON_TEXT = {"profile", "canonical_run_file", "seeds", "rule_list_versions", "parent_shas",
              "pattern_ids", "hunks", "env_fingerprint", "smell_mapping", "file_hashes",
              "function_names", "hidden_test_ids", "project_test_ids", "generation_metadata",
              "new_imports", "redefinitions"}


def describe(entity: str, name: str, python_type: type) -> Column:
    """Return the dictionary entry of one exported column."""
    kind = "json text" if name in _JSON_TEXT else _TYPES.get(python_type, "text")
    unit, description = _DESCRIPTIONS.get((entity, name)) or _DESCRIPTIONS.get(
        ("*", name)) or ("", name.replace("_", " "))
    return Column(name, kind, unit, description)


_ISO = "ISO 8601 UTC"
_KEY = "64-hex HMAC-SHA-256 identity key"
_DESCRIPTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("*", "run_id"): ("", "Run identifier (FR-48)"),
    ("*", "repository_id"): ("", "Repository record key"),
    ("*", "unit_id"): ("", "Unit (repository-window) record key"),
    ("*", "commit_id"): ("", "Commit record key"),
    ("*", "item_id"): ("", "Debt item record key"),
    ("*", "bundle_id"): ("", "Bundle record key"),
    ("*", "gen_id"): ("", "Generation ID from the bundle manifest"),
    ("*", "task_id"): ("", "Task ID from tasks.jsonl"),
    ("*", "variant_id"): ("", "Variant record key"),
    ("*", "tau"): ("similarity", "Counterpart similarity threshold tau"),
    ("*", "url"): ("", "Repository URL as written in the run file"),
    ("*", "message"): ("", "Message text"),
    ("*", "symbol"): ("", "Pylint message symbol"),
    ("*", "message_id"): ("", "Pylint message ID"),
    ("*", "line"): ("line number", "1-based line number"),
    ("*", "smell_type"): ("", "Smell type from the smell mapping (T1, T2, ...)"),
    ("*", "count"): ("count", "Number of messages on added lines"),
    ("*", "module_sha256"): ("sha256", "SHA-256 of the modified module text (UTF-8, LF)"),
    ("*", "tree_sha256"): ("sha256", "SHA-256 of the executed tree (tree_sha256)"),
    ("*", "import_status"): ("", "ok, error or not_attempted"),
    ("*", "python_version"): ("", "Python version"),
    ("*", "prepare_module_version"): ("", "prepare_module version"),
    ("*", "harness_version"): ("", "Research harness version"),
    ("*", "project"): ("", "Project the task or variant belongs to"),
    ("*", "path"): ("", "File path in the repository (POSIX)"),
    ("*", "blob"): ("git blob SHA", "Blob of the file at the introducing commit"),
    ("run", "kind"): ("", "rg1 or rg10"),
    ("run", "status"): ("", "complete or complete_with_exclusions"),
    ("run", "tdea_version"): ("", "TDEA version"),
    ("run", "pylint_version"): ("", "Pylint version (4.1.1)"),
    ("run", "astroid_version"): ("", "astroid version"),
    ("run", "radon_version"): ("", "radon version"),
    ("run", "git_version"): ("", "git version"),
    ("run", "gitpython_version"): ("", "GitPython version"),
    ("run", "profile_hash"): ("sha256", "Research profile hash (FR-51)"),
    ("run", "profile"): ("", "Research profile, canonical JSON"),
    ("run", "canonical_run_file"): ("", "Canonical run file without store and key_file"),
    ("run", "time_limit_seconds"): ("seconds", "Per-file lint time limit"),
    ("run", "w_months"): ("months", "Tracking window W"),
    ("run", "cutoff"): (_ISO, "Data cut-off from the run file, if set"),
    ("run", "seeds"): ("", "Seeds from the run file"),
    ("run", "key_fingerprint"): ("", "First 16 hex characters of SHA-256 of the key"),
    ("run", "rename_threshold"): ("percent", "Rename similarity threshold (D-11)"),
    ("run", "rule_list_versions"): ("", "Versions and SHA-256 of the rule lists used"),
    ("repository", "default_branch"): ("", "Default branch"),
    ("repository", "head_sha"): ("git commit SHA", "HEAD the history was read from"),
    ("repository", "cutoff"): (_ISO, "Effective cut-off for this repository"),
    ("unit", "window_start"): (_ISO, "Window start (inclusive)"),
    ("unit", "window_end"): (_ISO, "Window end (exclusive)"),
    ("unit", "sha_list_hash"): ("sha256", "Hash of the window's commit list, if any"),
    ("unit", "status"): ("", "Unit status"),
    ("unit", "selected_commits"): ("count", "Selected commits in the window"),
    ("unit", "introductions"): ("count", "Debt items introduced"),
    ("unit", "lint_failures"): ("count", "Files excluded because linting failed"),
    ("unit", "commits_after_cutoff"): ("count", "Commits ignored after the cut-off"),
    ("unit_summary", "disappearances"): ("count", "Items that disappeared"),
    ("unit_summary", "local_fixes"): ("count", "Disappearances labelled local fix (unvalidated)"),
    ("unit_summary", "local_fix_share"): ("ratio", "Local fixes / disappearances (unvalidated)"),
    ("unit_summary", "unvalidated"): ("", "Always true: local-fix labels are not validated"),
    ("commit", "sha"): ("git commit SHA", "Commit SHA"),
    ("commit", "first_parent_position"): ("index", "Position on the first-parent history"),
    ("commit", "parent_shas"): ("", "Parent SHAs, JSON list"),
    ("commit", "is_merge"): ("", "Merge commit (D-10)"),
    ("commit", "authored_at"): (_ISO, "Author timestamp"),
    ("commit", "committed_at"): (_ISO, "Committer timestamp"),
    ("commit", "author_email_key"): (_KEY, "Author e-mail key (author_id)"),
    ("commit", "committer_email_key"): (_KEY, "Committer e-mail key (committer_id)"),
    ("commit", "message"): ("", "Commit message with identities replaced by keys"),
    ("commit", "trace_status"): ("", "ai_traced, bot or non_traced (null: not classified)"),
    ("commit", "tool"): ("", "AI tool matched, if any"),
    ("commit", "method"): ("", "Where the AI trace was found: author, committer, trailer"),
    ("commit", "pattern_ids"): ("", "Matching pattern IDs, JSON list"),
    ("commit", "clock_anomaly"): ("", "Commit-level clock anomaly flag"),
    ("file_change", "change_id"): ("", "File change record key"),
    ("file_change", "change_type"): ("", "A, M, D or R"),
    ("file_change", "old_path"): ("", "Path before the change"),
    ("file_change", "new_path"): ("", "Path after the change"),
    ("file_change", "old_blob"): ("git blob SHA", "Blob before the change"),
    ("file_change", "new_blob"): ("git blob SHA", "Blob after the change"),
    ("file_change", "rename_similarity"): ("percent", "Rename similarity"),
    ("file_change", "is_package"): ("", "Passes the package-code filter"),
    ("file_change", "lines_added"): ("lines", "Lines added"),
    ("file_change", "lines_removed"): ("lines", "Lines removed"),
    ("file_change", "hunks"): ("", "Hunks, JSON"),
    ("debt_item", "introducing_commit_id"): ("", "Commit record that introduced the item"),
    ("debt_item", "item_key"): ("", "Symbol + normalised line text + scope (FR-53)"),
    ("debt_item", "category"): ("", "Pylint category"),
    ("debt_item", "scope"): ("", "Enclosing scope name"),
    ("debt_item", "normalised_text"): ("", "Normalised flagged-line text"),
    ("debt_item", "occurrence"): ("index", "Occurrence of the item key in the file"),
    ("debt_item", "introduced_at"): (_ISO, "Introduction date"),
    ("debt_item", "c1_commit_id"): ("", "Commit record of the disappearance (c1)"),
    ("debt_item", "c1_date"): (_ISO, "Date of c1"),
    ("debt_item", "time_to_event_days"): ("days", "Introduction to c1 or censoring"),
    ("debt_item", "censoring_date"): (_ISO, "Censoring date"),
    ("debt_item", "censoring_reason"): ("", "window or cut-off"),
    ("debt_item", "path_before_c1"): ("", "Path before c1"),
    ("debt_item", "blob_before_c1"): ("git blob SHA", "Blob before c1"),
    ("debt_item", "path_after_c1"): ("", "Path after c1"),
    ("debt_item", "blob_after_c1"): ("git blob SHA", "Blob after c1"),
    ("debt_item", "line_before_c1"): ("line number", "Warning line before c1"),
    ("debt_item", "text_before_c1"): ("", "Warning line text before c1"),
    ("debt_item", "clock_anomaly"): ("", "Negative item interval (FR-55)"),
    ("debt_item", "introducing_sha"): ("git commit SHA", "SHA of the introducing commit"),
    ("debt_item", "c1_sha"): ("git commit SHA", "SHA of c1, the disappearance commit"),
    ("disappearance", "bulk_a1"): ("", "Bulk sub-reason a1: lint or formatter config"),
    ("disappearance", "bulk_a2"): ("", "Bulk sub-reason a2: formatting sweep"),
    ("disappearance", "bulk_a3"): ("", "Bulk sub-reason a3: message pattern"),
    ("disappearance", "file_removed"): ("", "File removed at c1"),
    ("disappearance", "parse_failure_reason"): ("", "Why the version could not be parsed"),
    ("disappearance", "counterpart_method"): ("", "How the counterpart scope was found (D-1)"),
    ("disappearance", "counterpart_scope"): ("", "Counterpart scope at c1"),
    ("disappearance", "similarity"): ("ratio", "Counterpart similarity (D-2)"),
    ("disappearance", "touch_exact"): ("", "c1 touched the warning line (D-5)"),
    ("disappearance", "touch_near"): ("", "c1 changed within 3 lines (D-5)"),
    ("disappearance", "touch_scope"): ("", "c1 changed the scope (D-5)"),
    ("disappearance", "resolver_trace_status"): ("", "Trace status of c1"),
    ("disappearance", "resolver_tool"): ("", "AI tool of c1, if any"),
    ("disappearance", "same_name"): ("", "Introducer and resolver share the name key"),
    ("disappearance", "same_email"): ("", "Introducer and resolver share the e-mail key"),
    ("item_mode", "mode"): ("", "Disappearance mode a to f (unvalidated)"),
    ("item_mode", "local_fix"): ("", "Local-fix label (unvalidated)"),
    ("item_mode", "unvalidated"): ("", "Always true: modes are not validated"),
    ("base_rate", "ai_traced_count"): ("count", "Later AI-traced commits touching the file"),
    ("base_rate", "non_traced_count"): ("count", "Later non-traced commits touching the file"),
    ("base_rate", "ai_traced_share"): ("ratio", "AI-traced share of those commits"),
    ("base_rate", "window_end"): (_ISO, "End of the base-rate window"),
    ("covariate", "file_origin"): ("", "Trace status of the commit that added the file (D-6)"),
    ("covariate", "volatility"): ("commits per month", "Commits touching the file (D-7)"),
    ("covariate", "file_size"): ("lines", "Physical lines at c (D-9)"),
    ("covariate", "file_age_days"): ("days", "File age at c"),
    ("covariate", "baseline_density"): ("messages per SLOC", "Parent-version density (D-8)"),
    ("covariate", "commit_lines"): ("lines", "Lines added + removed in c (D-9)"),
    ("covariate", "commit_files"): ("count", "Files changed in c (D-9)"),
    ("covariate", "month"): ("YYYY-MM", "Month of introduction"),
    ("covariate", "author_id"): (_KEY, "Author e-mail key"),
    ("covariate", "account_tenure_days"): ("days", "Exploratory: days since the author's "
                                                   "first commit"),
    ("covariate", "prior_commits_to_file"): ("count", "Exploratory: author's earlier commits "
                                                      "to the file"),
    ("covariate", "later_activity"): ("count", "Exploratory: author's commits in W after c"),
    ("bundle", "schema_version"): ("", "Bundle schema version"),
    ("bundle", "env_fingerprint"): ("", "Harness execution environment, JSON"),
    ("bundle", "smell_mapping"): ("", "Smell mapping used, JSON"),
    ("bundle", "content_hash"): ("sha256", "Bundle content hash"),
    ("variant", "smell"): ("", "Smell the variant is built for"),
    ("variant", "condition"): ("", "clean, placebo or injected"),
    ("variant", "dose"): ("", "Dose, if any"),
    ("variant", "file_hashes"): ("", "SHA-256 of each variant file, JSON"),
    ("task", "target_module"): ("", "Module the output is inserted into"),
    ("task", "marker"): ("", "Insertion marker"),
    ("task", "function_names"): ("", "Functions the task asks for, JSON list"),
    ("task", "hidden_test_ids"): ("", "Declared hidden test IDs, JSON list"),
    ("task", "project_test_ids"): ("", "Declared project test IDs, JSON list"),
    ("declared_opportunity", "declared_count"): ("count", "Declared opportunities"),
    ("output", "replicate"): ("index", "Replicate number"),
    ("output", "run_order"): ("index", "Generation order"),
    ("output", "prompt_file"): ("", "Prompt file in the bundle"),
    ("output", "output_file"): ("", "Output file in the bundle"),
    ("output", "generation_metadata"): ("", "Generation metadata (C9), JSON"),
    ("output", "extraction_status"): ("", "none, inserted, multiple or unterminated"),
    ("output", "inserted_first_line"): ("line number", "First inserted line"),
    ("output", "inserted_last_line"): ("line number", "Last inserted line"),
    ("output", "n_added_lines"): ("lines", "Lines inserted"),
    ("output", "parses"): ("", "Module parses under Python 3.11"),
    ("output", "parse_error"): ("", "Parse error, if any"),
    ("output", "new_imports"): ("", "New imports, JSON list"),
    ("output", "redefinitions"): ("", "Redefined names, JSON list"),
    ("output", "insertable"): ("", "Insertable (FR-59)"),
    ("output", "evaluability"): ("", "ok or the reason the output is unevaluable"),
    ("output", "hidden_suite_result"): ("", "pass or fail; null if unevaluable"),
    ("output", "project_suite_result"): ("", "pass or fail; null if unevaluable"),
    ("output", "suite_result_source"): ("", "harness or rule"),
    ("execution", "import_error"): ("", "Exception type and message"),
    ("execution", "executed_at"): (_ISO, "When the harness executed the output"),
    ("test_result", "suite"): ("", "hidden or project"),
    ("test_result", "test_id"): ("", "Test ID"),
    ("test_result", "outcome"): ("", "pass, fail, error or timeout"),
    ("test_result", "duration_s"): ("seconds", "Test duration"),
    ("test_result", "source"): ("", "harness or rule"),
    ("detection", "detection_id"): ("", "Detection record key"),
    ("detection", "on_added_line"): ("", "Message is on an added line"),
    ("type_count", "count"): ("count", "Messages of the type on added lines; null on lint "
                                       "failure"),
    ("type_count", "declared_opportunities"): ("count", "Declared opportunities"),
    ("type_count", "is_target"): ("", "Type is the variant's target smell"),
    ("type_count", "rate"): ("per opportunity", "count / declared opportunities"),
    ("type_count", "exclusion_reason"): ("", "Why there is no rate, if none"),
}
