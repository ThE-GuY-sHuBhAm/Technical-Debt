"""The 21 research entities of research.db (FR-52, SRS Appendix C, Freeze section 7).

research.db has its own metadata (ResearchBase), separate from the four product
entities in tdea.db (FR-19) and from the two lint-cache entities in lintcache.db
(lint_cache.py); no foreign key crosses files. Entities, keys, attributes and
relationships follow Appendix C. Column names are its attribute names in snake case;
column types are implementation choices: lists and mappings are canonical JSON text
(canonical_json), timestamps are ISO 8601 UTC text (iso_utc), counts are integers and
rates floats. A run's identifier is an opaque text key (FR-48 derives it).

Two interpretations, recorded in the Task 6 report:

* RG10 keys from the bundle (task_id, gen_id) are scoped by their bundle, so the keys are
  (bundle_id, task_id) and (bundle_id, gen_id): one research.db can hold several runs,
  and every record reaches its run through one parent chain. Composite foreign keys keep
  each chain inside one bundle: an output's task and variant, a declared opportunity's
  task, and an execution's, test result's, detection's or count's output.
* commit.trace_status may be NULL, meaning not classified, while CL-03 cannot classify
  (Task 6 decision 4). NULL is never read as non_traced; once CL-03 is final, research
  runs classify before they store (FR-54).

No column holds a raw name or e-mail (NFR-27): identity columns hold 64-hex identity keys,
which a check constraint enforces.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import (Boolean, CheckConstraint, Float, ForeignKey, ForeignKeyConstraint,
                        Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ResearchBase(DeclarativeBase):
    """Declarative base whose metadata is research.db's alone."""


def canonical_json(value: object) -> str:
    """Return ``value`` as JSON with sorted keys and no spaces, for list-valued columns."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def iso_utc(moment: datetime) -> str:
    """Return a timezone-aware UTC datetime as ISO 8601 text."""
    if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
        raise ValueError("research timestamps must be timezone-aware UTC")
    return moment.isoformat()


def _key_check(column: str) -> CheckConstraint:
    """Allow only NULL or a 64-character lowercase hex identity key in ``column``.

    The type and byte-length tests stop a text or BLOB value that hides other bytes after
    a NUL, which SQLite's length() and GLOB would not see.
    """
    return CheckConstraint(
        f"{column} IS NULL OR (typeof({column}) = 'text' "
        f"AND length(CAST({column} AS BLOB)) = 64 AND {column} NOT GLOB '*[^0-9a-f]*')",
        name=f"ck_{column}_is_identity_key")


def _one_of(column: str, values: tuple[str, ...], nullable: bool = False) -> CheckConstraint:
    """Allow only the listed values (and NULL when ``nullable``) in ``column``."""
    allowed = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({allowed})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"ck_{column}_values")


# --- Run group -------------------------------------------------------------------------

class Run(ResearchBase):
    """One research run and its metadata (FR-52)."""

    __tablename__ = "run"
    __table_args__ = (_one_of("kind", ("rg1", "rg10")),)

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    tdea_version: Mapped[str] = mapped_column(String)
    python_version: Mapped[str] = mapped_column(String)
    pylint_version: Mapped[str] = mapped_column(String)
    astroid_version: Mapped[str] = mapped_column(String)
    radon_version: Mapped[str] = mapped_column(String)
    git_version: Mapped[str | None] = mapped_column(String)
    gitpython_version: Mapped[str | None] = mapped_column(String)
    profile_hash: Mapped[str] = mapped_column(String)
    profile: Mapped[str] = mapped_column(Text)
    canonical_run_file: Mapped[str] = mapped_column(Text)
    time_limit_seconds: Mapped[float | None] = mapped_column(Float)  # FR-51: recorded
    w_months: Mapped[int | None] = mapped_column(Integer)
    tau: Mapped[float | None] = mapped_column(Float)
    cutoff: Mapped[str | None] = mapped_column(String)
    seeds: Mapped[str | None] = mapped_column(Text)
    key_fingerprint: Mapped[str | None] = mapped_column(String)
    rename_threshold: Mapped[int | None] = mapped_column(Integer)
    rule_list_versions: Mapped[str | None] = mapped_column(Text)
    prepare_module_version: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[str | None] = mapped_column(String)
    ended_at: Mapped[str | None] = mapped_column(String)


# --- RG1 group -------------------------------------------------------------------------

class Repository(ResearchBase):
    """One repository of an RG1 run (FR-49)."""

    __tablename__ = "repository"

    repository_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.run_id"))
    url: Mapped[str] = mapped_column(String)
    default_branch: Mapped[str] = mapped_column(String)
    clone_time: Mapped[str] = mapped_column(String)
    head_sha: Mapped[str] = mapped_column(String)
    cutoff: Mapped[str] = mapped_column(String)


class Unit(ResearchBase):
    """One repository-window unit (NFR-25)."""

    __tablename__ = "unit"

    unit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.run_id"))
    repository_id: Mapped[int] = mapped_column(ForeignKey("repository.repository_id"))
    window_start: Mapped[str] = mapped_column(String)
    window_end: Mapped[str] = mapped_column(String)
    sha_list_hash: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    completed_at: Mapped[str | None] = mapped_column(String)
    selected_commits: Mapped[int | None] = mapped_column(Integer)
    introductions: Mapped[int | None] = mapped_column(Integer)
    lint_failures: Mapped[int | None] = mapped_column(Integer)
    commits_after_cutoff: Mapped[int | None] = mapped_column(Integer)


class UnitSummary(ResearchBase):
    """Disappearance summary of a unit at one tau (FR-56)."""

    __tablename__ = "unit_summary"

    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.unit_id"), primary_key=True)
    tau: Mapped[float] = mapped_column(Float, primary_key=True)
    disappearances: Mapped[int] = mapped_column(Integer)
    local_fixes: Mapped[int] = mapped_column(Integer)
    local_fix_share: Mapped[float | None] = mapped_column(Float)


class Commit(ResearchBase):
    """One first-parent commit, with identity keys only (FR-49, FR-54, NFR-27)."""

    __tablename__ = "commit"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha"),
        _key_check("author_name_key"), _key_check("author_email_key"),
        _key_check("committer_name_key"), _key_check("committer_email_key"),
        _one_of("trace_status", ("ai_traced", "bot", "non_traced"), nullable=True),
        _one_of("method", ("author", "committer", "trailer"), nullable=True),
    )

    commit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repository.repository_id"))
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("unit.unit_id"))
    sha: Mapped[str] = mapped_column(String)
    first_parent_position: Mapped[int] = mapped_column(Integer)
    parent_shas: Mapped[str] = mapped_column(Text)
    is_merge: Mapped[bool] = mapped_column(Boolean)
    authored_at: Mapped[str] = mapped_column(String)
    committed_at: Mapped[str] = mapped_column(String)
    author_name_key: Mapped[str | None] = mapped_column(String)
    author_email_key: Mapped[str | None] = mapped_column(String)
    committer_name_key: Mapped[str | None] = mapped_column(String)
    committer_email_key: Mapped[str | None] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    trace_status: Mapped[str | None] = mapped_column(String)
    tool: Mapped[str | None] = mapped_column(String)
    method: Mapped[str | None] = mapped_column(String)
    pattern_ids: Mapped[str | None] = mapped_column(Text)
    clock_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)


class FileChange(ResearchBase):
    """One changed file of a commit against its first parent (FR-50)."""

    __tablename__ = "file_change"
    __table_args__ = (_one_of("change_type", ("A", "M", "D", "R")),)

    change_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commit.commit_id"))
    change_type: Mapped[str] = mapped_column(String)
    old_path: Mapped[str | None] = mapped_column(String)
    new_path: Mapped[str | None] = mapped_column(String)
    old_blob: Mapped[str | None] = mapped_column(String)
    new_blob: Mapped[str | None] = mapped_column(String)
    rename_similarity: Mapped[int | None] = mapped_column(Integer)
    is_package: Mapped[bool | None] = mapped_column(Boolean)
    lines_added: Mapped[int | None] = mapped_column(Integer)
    lines_removed: Mapped[int | None] = mapped_column(Integer)
    hunks: Mapped[str] = mapped_column(Text)


class DebtItem(ResearchBase):
    """One introduced debt item and its tracking fields (FR-53, FR-55)."""

    __tablename__ = "debt_item"
    __table_args__ = (_one_of("censoring_reason", ("window", "cut-off"), nullable=True),)

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("unit.unit_id"))
    introducing_commit_id: Mapped[int] = mapped_column(ForeignKey("commit.commit_id"))
    item_key: Mapped[str] = mapped_column(Text)
    symbol: Mapped[str] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    line: Mapped[int] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String)
    normalised_text: Mapped[str] = mapped_column(Text)
    occurrence: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String)
    blob: Mapped[str] = mapped_column(String)
    introduced_at: Mapped[str] = mapped_column(String)
    c1_commit_id: Mapped[int | None] = mapped_column(ForeignKey("commit.commit_id"))
    c1_date: Mapped[str | None] = mapped_column(String)
    time_to_event_days: Mapped[float | None] = mapped_column(Float)
    censoring_date: Mapped[str | None] = mapped_column(String)
    censoring_reason: Mapped[str | None] = mapped_column(String)
    path_before_c1: Mapped[str | None] = mapped_column(String)
    blob_before_c1: Mapped[str | None] = mapped_column(String)
    path_after_c1: Mapped[str | None] = mapped_column(String)
    blob_after_c1: Mapped[str | None] = mapped_column(String)
    line_before_c1: Mapped[int | None] = mapped_column(Integer)
    text_before_c1: Mapped[str | None] = mapped_column(Text)
    clock_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)


class Disappearance(ResearchBase):
    """Signals of a debt item's disappearance at c1 (FR-56)."""

    __tablename__ = "disappearance"

    item_id: Mapped[int] = mapped_column(ForeignKey("debt_item.item_id"), primary_key=True)
    bulk_a1: Mapped[bool] = mapped_column(Boolean)
    bulk_a2: Mapped[bool] = mapped_column(Boolean)
    bulk_a3: Mapped[bool] = mapped_column(Boolean)
    file_removed: Mapped[bool] = mapped_column(Boolean)
    parse_failure_reason: Mapped[str | None] = mapped_column(String)
    counterpart_method: Mapped[str | None] = mapped_column(String)
    counterpart_scope: Mapped[str | None] = mapped_column(String)
    similarity: Mapped[float | None] = mapped_column(Float)
    touch_exact: Mapped[bool | None] = mapped_column(Boolean)
    touch_near: Mapped[bool | None] = mapped_column(Boolean)
    touch_scope: Mapped[bool | None] = mapped_column(Boolean)
    resolver_trace_status: Mapped[str | None] = mapped_column(String)
    resolver_tool: Mapped[str | None] = mapped_column(String)
    same_name: Mapped[bool | None] = mapped_column(Boolean)
    same_email: Mapped[bool | None] = mapped_column(Boolean)


class ItemMode(ResearchBase):
    """A disappearance's mode at one tau; always unvalidated (FR-56)."""

    __tablename__ = "item_mode"
    __table_args__ = (_one_of("mode", ("a", "b", "c", "d", "e", "f")),)

    item_id: Mapped[int] = mapped_column(ForeignKey("disappearance.item_id"), primary_key=True)
    tau: Mapped[float] = mapped_column(Float, primary_key=True)
    mode: Mapped[str] = mapped_column(String)
    local_fix: Mapped[bool] = mapped_column(Boolean)
    unvalidated: Mapped[bool] = mapped_column(Boolean, default=True)


class BaseRate(ResearchBase):
    """Later non-bot commits touching an item's file within W, by provenance (FR-56)."""

    __tablename__ = "base_rate"

    item_id: Mapped[int] = mapped_column(ForeignKey("debt_item.item_id"), primary_key=True)
    ai_traced_count: Mapped[int] = mapped_column(Integer)
    non_traced_count: Mapped[int] = mapped_column(Integer)
    ai_traced_share: Mapped[float | None] = mapped_column(Float)
    window_end: Mapped[str] = mapped_column(String)


class Covariate(ResearchBase):
    """Covariates and exploratory fields of a debt item (FR-57)."""

    __tablename__ = "covariate"
    __table_args__ = (_key_check("author_id"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("debt_item.item_id"), primary_key=True)
    file_origin: Mapped[str | None] = mapped_column(String)
    volatility: Mapped[float | None] = mapped_column(Float)
    file_size: Mapped[int | None] = mapped_column(Integer)
    file_age_days: Mapped[float | None] = mapped_column(Float)
    baseline_density: Mapped[float | None] = mapped_column(Float)
    commit_lines: Mapped[int | None] = mapped_column(Integer)
    commit_files: Mapped[int | None] = mapped_column(Integer)
    month: Mapped[str | None] = mapped_column(String)
    author_id: Mapped[str | None] = mapped_column(String)
    account_tenure_days: Mapped[float | None] = mapped_column(Float)
    prior_commits_to_file: Mapped[int | None] = mapped_column(Integer)
    later_activity: Mapped[int | None] = mapped_column(Integer)


# --- RG10 group ------------------------------------------------------------------------

class Bundle(ResearchBase):
    """The one RG10 bundle of an RG10 run (FR-58)."""

    __tablename__ = "bundle"

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.run_id"), unique=True)
    path: Mapped[str] = mapped_column(String)
    schema_version: Mapped[str] = mapped_column(String)
    harness_version: Mapped[str] = mapped_column(String)
    prepare_module_version: Mapped[str] = mapped_column(String)
    env_fingerprint: Mapped[str] = mapped_column(Text)
    smell_mapping: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String)


class Variant(ResearchBase):
    """One condition variant of a bundle (FR-58)."""

    __tablename__ = "variant"
    # (bundle_id, variant_id) is unique because variant_id is; naming it lets an output's
    # variant be required to belong to the output's own bundle.
    __table_args__ = (UniqueConstraint("bundle_id", "variant_id", name="uq_variant_bundle"),)

    variant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bundle_id: Mapped[int] = mapped_column(ForeignKey("bundle.bundle_id"))
    smell: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(String)
    dose: Mapped[str | None] = mapped_column(String)
    project: Mapped[str] = mapped_column(String)
    file_hashes: Mapped[str] = mapped_column(Text)


class Task(ResearchBase):
    """One task of a bundle, keyed by its bundle and task ID (FR-58)."""

    __tablename__ = "task"

    bundle_id: Mapped[int] = mapped_column(ForeignKey("bundle.bundle_id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    project: Mapped[str] = mapped_column(String)
    target_module: Mapped[str] = mapped_column(String)
    marker: Mapped[str] = mapped_column(String)
    function_names: Mapped[str] = mapped_column(Text)
    hidden_test_ids: Mapped[str] = mapped_column(Text)
    project_test_ids: Mapped[str] = mapped_column(Text)


class DeclaredOpportunity(ResearchBase):
    """A task's declared opportunity count for one smell type (FR-58, FR-61)."""

    __tablename__ = "declared_opportunity"
    __table_args__ = (ForeignKeyConstraint(["bundle_id", "task_id"],
                                           ["task.bundle_id", "task.task_id"]),)

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    smell_type: Mapped[str] = mapped_column(String, primary_key=True)
    declared_count: Mapped[int] = mapped_column(Integer)


class Output(ResearchBase):
    """One model output and its extraction, static and insertability results (FR-59).

    gen_id is unique within its bundle; the output's task and variant must belong to the
    same bundle (composite foreign keys).
    """

    __tablename__ = "output"
    __table_args__ = (ForeignKeyConstraint(["bundle_id", "task_id"],
                                           ["task.bundle_id", "task.task_id"]),
                      ForeignKeyConstraint(["bundle_id", "variant_id"],
                                           ["variant.bundle_id", "variant.variant_id"]))

    bundle_id: Mapped[int] = mapped_column(ForeignKey("bundle.bundle_id"), primary_key=True)
    gen_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String)
    variant_id: Mapped[int] = mapped_column(Integer)
    replicate: Mapped[int] = mapped_column(Integer)
    run_order: Mapped[int] = mapped_column(Integer)
    prompt_file: Mapped[str] = mapped_column(String)
    output_file: Mapped[str] = mapped_column(String)
    generation_metadata: Mapped[str | None] = mapped_column(Text)
    extraction_status: Mapped[str | None] = mapped_column(String)
    inserted_first_line: Mapped[int | None] = mapped_column(Integer)
    inserted_last_line: Mapped[int | None] = mapped_column(Integer)
    n_added_lines: Mapped[int | None] = mapped_column(Integer)
    parses: Mapped[bool | None] = mapped_column(Boolean)
    parse_error: Mapped[str | None] = mapped_column(Text)
    new_imports: Mapped[str | None] = mapped_column(Text)
    redefinitions: Mapped[str | None] = mapped_column(Text)
    module_sha256: Mapped[str | None] = mapped_column(String)
    tree_sha256: Mapped[str | None] = mapped_column(String)
    import_status: Mapped[str | None] = mapped_column(String)
    insertable: Mapped[bool | None] = mapped_column(Boolean)
    evaluability: Mapped[str | None] = mapped_column(String)
    hidden_suite_result: Mapped[str | None] = mapped_column(String)
    project_suite_result: Mapped[str | None] = mapped_column(String)
    suite_result_source: Mapped[str | None] = mapped_column(String)


def _output_key() -> ForeignKeyConstraint:
    """Return a new foreign key to the output an entity belongs to (bundle_id, gen_id)."""
    return ForeignKeyConstraint(["bundle_id", "gen_id"], ["output.bundle_id", "output.gen_id"])


class Execution(ResearchBase):
    """The harness's execution record for one output (FR-59, Section 4.5.3)."""

    __tablename__ = "execution"
    __table_args__ = (_output_key(),)

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gen_id: Mapped[str] = mapped_column(String, primary_key=True)
    module_sha256: Mapped[str] = mapped_column(String)
    tree_sha256: Mapped[str] = mapped_column(String)
    python_version: Mapped[str] = mapped_column(String)
    import_status: Mapped[str] = mapped_column(String)
    import_error: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[str | None] = mapped_column(String)
    harness_version: Mapped[str | None] = mapped_column(String)
    prepare_module_version: Mapped[str] = mapped_column(String)


class TestResult(ResearchBase):
    """One hidden or project test outcome of an output (FR-60)."""

    __tablename__ = "test_result"
    __test__ = False  # not a pytest test class
    __table_args__ = (_output_key(), _one_of("suite", ("hidden", "project")),
                      _one_of("outcome", ("pass", "fail", "error", "timeout")))

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gen_id: Mapped[str] = mapped_column(String, primary_key=True)
    suite: Mapped[str] = mapped_column(String, primary_key=True)
    test_id: Mapped[str] = mapped_column(String, primary_key=True)
    outcome: Mapped[str] = mapped_column(String)
    duration_s: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String)


class Detection(ResearchBase):
    """One Pylint message on a modified module (FR-61)."""

    __tablename__ = "detection"
    __table_args__ = (_output_key(),)

    detection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bundle_id: Mapped[int] = mapped_column(Integer)
    gen_id: Mapped[str] = mapped_column(String)
    symbol: Mapped[str] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String)
    line: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    on_added_line: Mapped[bool] = mapped_column(Boolean)


class SymbolCount(ResearchBase):
    """Count of one symbol on an output's added lines (FR-61)."""

    __tablename__ = "symbol_count"
    __table_args__ = (_output_key(),)

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gen_id: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)


class TypeCount(ResearchBase):
    """Count and rate of one smell type for an output (FR-61)."""

    __tablename__ = "type_count"
    __table_args__ = (_output_key(),)

    bundle_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gen_id: Mapped[str] = mapped_column(String, primary_key=True)
    smell_type: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int | None] = mapped_column(Integer)
    declared_opportunities: Mapped[int] = mapped_column(Integer)
    is_target: Mapped[bool] = mapped_column(Boolean)
    rate: Mapped[float | None] = mapped_column(Float)
    exclusion_reason: Mapped[str | None] = mapped_column(String)


RESEARCH_TABLES = tuple(sorted(ResearchBase.metadata.tables))
