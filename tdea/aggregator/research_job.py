"""ResearchJob: unit planning, resume and one transaction per unit (FR-48, FR-49, NFR-25).

plan_units() expands an RG1 run file into units, one per repository and window, in
run-file order (Freeze section 8, step 3). run() starts a run or resumes the incomplete
run with the same identifier: units already complete are skipped and never recomputed;
the others are analysed through the analyzer's ResearchEngine, one repository at a time,
and each unit is committed in one transaction, so a killed unit leaves no record and a
committed unit is never written again (NFR-25, NFR-08). Starting a completed run again
does nothing.

What research.db holds, and when (Task 7 Day 6 readings):

* The run row (status "running", its FR-52 metadata and start time) is written once,
  before the first unit; the run becomes "complete", with its end time, in the
  transaction after its last unit.
* A unit row is written only in its own transaction, with status "complete", its
  completion time and counts. unit.repository_id is required by the frozen schema, and
  the repository row exists only after the first clone, so planned units are not stored:
  a unit without a row is planned, and status derives the planned units from the run's
  canonical run file.
* The repository row (URL, default branch, clone time, HEAD read from, cut-off) and the
  repository's whole first-parent history (commits with identity keys only, and file
  changes with hunks and the package-filter answer) are written in the transaction of
  the repository's first unit. Each unit's transaction then sets unit_id on its selected
  commits and adds its debt items with their tracking fields and, from Day 7, each
  disappearance with its signals, its four item modes (one per tau, unvalidated = true),
  every item's base rate and the unit's four summaries (FR-56), and, from Day 8, every
  item's covariate row (FR-57; account tenure null, deferred pending the RM decision on
  Q9). A fail-closed FR-56 or FR-57 stop happens before the transaction, so nothing of
  that unit is stored.
* Before a stored run is resumed or reported complete, its completed units are checked
  (Task 7 Day 8 decisions R3.2, R3.3 and A): every debt item of a completed unit has its
  covariate row (exactly one: the row's key is the item) and every completed unit has
  exactly one summary per tau. A store written before Day 8 fails the check, and the
  run is refused through the unresolved-code path (exit 1 placeholder, decision R4.3)
  before anything is written, repaired or recomputed. The TDEA version is unchanged.
  ResearchService runs check_store() on a read-only store before it takes the lock, so a
  refused run writes nothing at all, and again under the lock before it touches the
  clone folder or the lint cache or creates this job.
* A repository that already has a row is read again from its recorded HEAD and cut-off;
  if the history read differs from the stored one, the run is refused as "history
  changed" (FR-49, exit 3).
* commit.clock_anomaly keeps its schema default (false): FR-55 flags a negative item
  interval (debt_item.clock_anomaly), and no frozen text defines a commit-level flag.

The clone of a repository is made in the run's clone folder and deleted after its last
pending unit, and also when a unit fails (FR-10); a clone left by a killed process is
deleted by the next run, which ResearchService starts by clearing that folder under the
lock. The job takes no lock and reads no environment: ResearchService does that before
it creates a job.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tdea.aggregator.research_models import (BaseRate, Commit, Covariate, DebtItem,
                                             Disappearance, FileChange, ItemMode, Repository,
                                             Run, Unit, UnitSummary, canonical_json, iso_utc)
from tdea.aggregator.research_store import ResearchStore
from tdea.aggregator.run_file import RepositorySpec, RunFile, WindowSpec
from tdea.analyzer.research.engine import (RecordedRepository, RepositoryPlan,
                                           ResearchEngine, UnitOutcome, UnitWindow)
from tdea.analyzer.research.profile import LintCachePort
from tdea.analyzer.research.rg1.defaults import TAUS
from tdea.analyzer.research.rg1.disappearance import LOCAL_FIX
from tdea.analyzer.research.rg1.unit_run import ChangeRecord, RepositoryRecord, TrackedItem

RUNNING = "running"
COMPLETE = "complete"
PLANNED = "planned"
_BATCH = 500  # SHAs per UPDATE, well under SQLite's oldest variable limit (999)


class HistoryMismatch(Exception):
    """The history read again differs from the one stored for this run (FR-49, exit 3)."""

    exit_code = 3

    def __init__(self, url: str) -> None:
        """Name the repository."""
        self.error_type = "History changed"
        self.cause = (f"The first-parent history read again from {url} differs from the one "
                      "this run recorded, so the run cannot be resumed (FR-49).")
        self.action = ("Restore the repository's history, or start a new run with another "
                       "run file.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


class StoreIncomplete(Exception):
    """A stored run lacks records its completed units must hold (Task 7 Day 8, R3.2).

    No exit code is agreed for it: it takes Day 6's unresolved-code path, like the
    fail-closed stops (decision R4.3), so the command line prints the three parts, the
    undecided note and placeholder status 1.
    """

    exit_code = None
    open_item = None
    category = "research store missing required records"

    def __init__(self, run_id: str, units: int, items: int, summaries: int) -> None:
        """Name the run and count what is missing."""
        self.error_type = "Research store incomplete"
        self.cause = (f"Run {run_id} has {units} completed unit(s) without the records this "
                      f"TDEA build requires: {items} debt item(s) without a covariate row "
                      f"(FR-57) and {summaries} unit(s) without exactly one summary per tau "
                      "(FR-56). Such a store was written by an earlier build or changed "
                      "outside TDEA, so the run is neither resumed nor reported complete.")
        self.action = ("Keep this store as it is and give the run file a new store path; "
                       "TDEA does not repair or recompute completed units.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


@dataclass(frozen=True)
class PlannedUnit:
    """One unit of a run: a repository and one of its windows, numbered from 1."""

    number: int
    repository: RepositorySpec
    window: WindowSpec

    @property
    def sha_list_hash(self) -> str | None:
        """Return SHA-256 of the window's SHA list as canonical JSON, or None without one."""
        if self.window.commits is None:
            return None
        return hashlib.sha256(canonical_json(list(self.window.commits)).encode()).hexdigest()

    @property
    def label(self) -> str:
        """Return a short description: repository and window."""
        return (f"{self.repository.owner}/{self.repository.name}, "
                f"{self.window.start.date()} to {self.window.end.date()}")


@dataclass(frozen=True)
class UnitCounts:
    """The counts a unit row records."""

    selected_commits: int
    introductions: int
    lint_failures: int
    commits_after_cutoff: int


@dataclass(frozen=True)
class RunContext:
    """What a run needs besides the run file; the key and token stay in memory."""

    run_id: str
    run_file: RunFile
    canonical_run_file: str
    tdea_version: str
    key: bytes
    key_fingerprint: str
    metadata: dict[str, object]
    lint_cache: LintCachePort | None = None
    token: str | None = None
    clone_root: Path | None = None  # None: the system temporary directory

    def __repr__(self) -> str:
        """Never show the key or the token."""
        return f"RunContext(run_id={self.run_id!r}, <key and token withheld>)"


@dataclass(frozen=True)
class RunResult:
    """What one start of a run did."""

    run_id: str
    total_units: int
    completed_now: int
    skipped: int
    already_complete: bool


class RunObserver:
    """Receives a run's progress; every method does nothing unless overridden."""

    def run_identified(self, run_id: str) -> None:
        """Receive the run identifier, which is reported first (FR-48)."""

    def run_starting(self, run_id: str, total: int, complete: int) -> None:
        """Receive the start (complete == 0) or resumption (complete > 0) of a run."""

    def unit_skipped(self, unit: PlannedUnit, total: int) -> None:
        """Receive a unit that is already complete and is skipped (NFR-25)."""

    def unit_written(self, unit: PlannedUnit, total: int) -> None:
        """Receive a unit whose records are written but not yet committed."""

    def unit_completed(self, unit: PlannedUnit, total: int, counts: UnitCounts) -> None:
        """Receive a unit whose transaction committed, with its counts."""

    def run_completed(self, run_id: str, total: int, already: bool) -> None:
        """Receive the end of a run; ``already`` if nothing was left to do."""


def plan_units(run_file: RunFile) -> tuple[PlannedUnit, ...]:
    """Return the run's units, one per repository and window, in run-file order."""
    units = []
    for repository in run_file.repositories:
        for window in repository.windows:
            units.append(PlannedUnit(len(units) + 1, repository, window))
    return tuple(units)


class ResearchJob:
    """Plans a run's units, skips completed ones and commits each unit in one transaction."""

    def __init__(self, store: ResearchStore, engine: ResearchEngine,
                 clock: Callable[[], datetime], observer: RunObserver | None = None) -> None:
        """Write to ``store``, analyse through ``engine`` and time with ``clock`` (UTC)."""
        self._store = store
        self._engine = engine
        self._clock = clock
        self._observer = observer if observer is not None else RunObserver()

    def run(self, context: RunContext) -> RunResult:
        """Start or resume the run and process every pending unit."""
        units = plan_units(context.run_file)
        total = len(units)
        if self._start(context) == COMPLETE:
            self._observer.run_completed(context.run_id, total, True)
            return RunResult(context.run_id, total, 0, total, True)
        done = self._completed_keys(context.run_id)
        pending = [unit for unit in units if _key(unit) not in done]
        self._observer.run_starting(context.run_id, total, total - len(pending))
        for unit in units:
            if _key(unit) in done:
                self._observer.unit_skipped(unit, total)
        for repository in context.run_file.repositories:
            mine = [unit for unit in pending if unit.repository is repository]
            if mine:
                self._run_repository(context, repository, mine, total)
        self._finish(context.run_id)
        self._observer.run_completed(context.run_id, total, False)
        return RunResult(context.run_id, total, len(pending), total - len(pending), False)

    def _start(self, context: RunContext) -> str:
        """Create the run row if it is new; return the run's status."""
        with self._store.transaction() as session:
            run = session.get(Run, context.run_id)
            if run is not None:
                if run.canonical_run_file != context.canonical_run_file:
                    raise RuntimeError(f"run {context.run_id} holds another run file")
                return run.status
            session.add(_run_row(context, iso_utc(self._clock())))
            return RUNNING

    def _completed_keys(self, run_id: str) -> set[tuple[str, str]]:
        """Return (URL, window start) of every completed unit of the run."""
        with self._store.reader() as session:
            rows = session.execute(
                select(Repository.url, Unit.window_start)
                .join(Repository, Unit.repository_id == Repository.repository_id)
                .where(Unit.run_id == run_id, Unit.status == COMPLETE)).all()
        return set(map(tuple, rows))

    def _run_repository(self, context: RunContext, repository: RepositorySpec,
                        units: Sequence[PlannedUnit], total: int) -> None:
        """Clone one repository, run its pending units and delete the clone (FR-10)."""
        recorded = self._recorded(context.run_id, repository.url)
        session = self._engine.open_repository(
            RepositoryPlan(repository.url, repository.head, context.run_file.cutoff, recorded),
            key=context.key, token=context.token, lint_cache=context.lint_cache,
            clone_root=context.clone_root)
        try:
            session.check_selections([_window(window) for window in repository.windows])
            if recorded is not None and \
                    self._stored_shas(context.run_id, repository.url) != session.history_shas:
                raise HistoryMismatch(repository.url)
            for unit in units:
                outcome = self._engine.run_rg1_unit(session, _window(unit.window),
                                                    context.run_file.w_months)
                counts = self._commit_unit(context, unit, session.record, outcome, total)
                self._observer.unit_completed(unit, total, counts)
        finally:
            session.close()

    def _recorded(self, run_id: str, url: str) -> RecordedRepository | None:
        """Return the stored repository record of this run, if its first unit committed."""
        with self._store.reader() as session:
            row = session.scalars(select(Repository).filter_by(run_id=run_id, url=url)
                                  ).one_or_none()
        if row is None:
            return None
        return RecordedRepository(row.head_sha, datetime.fromisoformat(row.cutoff),
                                  datetime.fromisoformat(row.clone_time), row.default_branch)

    def _stored_shas(self, run_id: str, url: str) -> tuple[str, ...]:
        """Return the stored history of a repository, root to HEAD."""
        with self._store.reader() as session:
            return tuple(session.scalars(
                select(Commit.sha).join(Repository,
                                        Commit.repository_id == Repository.repository_id)
                .where(Repository.run_id == run_id, Repository.url == url)
                .order_by(Commit.first_parent_position)).all())

    def _commit_unit(self, context: RunContext, unit: PlannedUnit, record: RepositoryRecord,
                     outcome: UnitOutcome, total: int) -> UnitCounts:
        """Write one unit, and its repository's history if new, in one transaction."""
        counts = UnitCounts(len(outcome.selected), len(outcome.items), outcome.lint_failures,
                            outcome.commits_after_cutoff)
        with self._store.transaction() as session:
            repository = session.scalars(select(Repository).filter_by(
                run_id=context.run_id, url=record.url)).one_or_none()
            if repository is None:
                repository = _add_repository(session, context.run_id, record)
            commit_ids = dict(session.execute(
                select(Commit.sha, Commit.commit_id)
                .filter_by(repository_id=repository.repository_id)).all())
            start = iso_utc(unit.window.start)
            if session.scalars(select(Unit).filter_by(
                    run_id=context.run_id, repository_id=repository.repository_id,
                    window_start=start)).first() is not None:
                raise RuntimeError(f"unit {unit.number} is already stored")
            row = Unit(run_id=context.run_id, repository_id=repository.repository_id,
                       window_start=start, window_end=iso_utc(unit.window.end),
                       sha_list_hash=unit.sha_list_hash, status=COMPLETE,
                       completed_at=iso_utc(self._clock()),
                       selected_commits=counts.selected_commits,
                       introductions=counts.introductions,
                       lint_failures=counts.lint_failures,
                       commits_after_cutoff=counts.commits_after_cutoff)
            session.add(row)
            session.flush()
            _select_commits(session, repository.repository_id, row.unit_id, outcome.selected)
            items = [_debt_item(row.unit_id, item, commit_ids) for item in outcome.items]
            session.add_all(items)
            session.flush()
            _add_outcomes(session, row.unit_id, items, outcome)
            self._observer.unit_written(unit, total)
        return counts

    def _finish(self, run_id: str) -> None:
        """Mark the run complete, with its end time."""
        with self._store.transaction() as session:
            run = session.get(Run, run_id)
            run.status = COMPLETE
            run.ended_at = iso_utc(self._clock())


def check_store(store: ResearchStore, run_id: str) -> None:
    """Raise StoreIncomplete if a completed unit of the run lacks required records.

    Read only, so it also runs on a store opened with ResearchStore.read_only(): every
    debt item of a completed unit has its covariate row (one at most: the row's key is the
    item) and every completed unit has exactly one summary per tau (Day 8, R3.2).
    """
    with store.reader() as session:
        completed = select(Unit.unit_id).where(Unit.run_id == run_id, Unit.status == COMPLETE)
        items = Counter(session.scalars(
            select(DebtItem.unit_id)
            .outerjoin(Covariate, Covariate.item_id == DebtItem.item_id)
            .where(DebtItem.unit_id.in_(completed), Covariate.item_id.is_(None))).all())
        taus: dict[int, list[float]] = {unit_id: [] for unit_id
                                        in session.scalars(completed).all()}
        for unit_id, tau in session.execute(
                select(UnitSummary.unit_id, UnitSummary.tau)
                .where(UnitSummary.unit_id.in_(completed))).all():
            taus[unit_id].append(tau)
    summaries = {unit_id for unit_id, found in taus.items() if sorted(found) != sorted(TAUS)}
    if items or summaries:
        raise StoreIncomplete(run_id, len(set(items) | summaries), sum(items.values()),
                              len(summaries))


def _key(unit: PlannedUnit) -> tuple[str, str]:
    """Return how a unit is found in research.db: URL as written and window start."""
    return unit.repository.url, iso_utc(unit.window.start)


def _window(window: WindowSpec) -> UnitWindow:
    """Return the analyzer's form of a run-file window."""
    return UnitWindow(window.start, window.end, window.commits)


def _run_row(context: RunContext, started_at: str) -> Run:
    """Return the run row with its FR-52 metadata."""
    run_file, metadata = context.run_file, context.metadata
    return Run(
        run_id=context.run_id, kind=run_file.kind, status=RUNNING,
        tdea_version=context.tdea_version,
        python_version=metadata["python_version"], pylint_version=metadata["pylint_version"],
        astroid_version=metadata["astroid_version"], radon_version=metadata["radon_version"],
        git_version=metadata["git_version"], gitpython_version=metadata["gitpython_version"],
        profile_hash=metadata["profile_hash"], profile=canonical_json(metadata["profile"]),
        canonical_run_file=context.canonical_run_file,
        time_limit_seconds=metadata["time_limit_seconds"], w_months=run_file.w_months,
        tau=run_file.tau,
        cutoff=iso_utc(run_file.cutoff) if run_file.cutoff is not None else None,
        seeds=canonical_json(dict(run_file.seeds)), key_fingerprint=context.key_fingerprint,
        rename_threshold=metadata["rename_threshold"],
        rule_list_versions=canonical_json(metadata["rule_list_versions"]),
        prepare_module_version=metadata["prepare_module_version"], started_at=started_at)


def _add_repository(session: Session, run_id: str, record: RepositoryRecord) -> Repository:
    """Add the repository row and its whole first-parent history."""
    repository = Repository(run_id=run_id, url=record.url, default_branch=record.default_branch,
                            clone_time=iso_utc(record.clone_time), head_sha=record.head_sha,
                            cutoff=iso_utc(record.cutoff))
    session.add(repository)
    session.flush()
    commits = [Commit(
        repository_id=repository.repository_id, unit_id=None, sha=commit.sha,
        first_parent_position=commit.position, parent_shas=canonical_json(list(commit.parents)),
        is_merge=commit.is_merge, authored_at=iso_utc(commit.authored_at),
        committed_at=iso_utc(commit.committed_at), author_name_key=commit.author_name_key,
        author_email_key=commit.author_email_key, committer_name_key=commit.committer_name_key,
        committer_email_key=commit.committer_email_key, message=commit.message,
        trace_status=commit.provenance.status.value, tool=commit.provenance.tool,
        method=commit.provenance.method.value if commit.provenance.method else None,
        pattern_ids=canonical_json(list(commit.provenance.pattern_ids)), clock_anomaly=False)
        for commit in record.commits]
    session.add_all(commits)
    session.flush()
    session.add_all(_file_change(row.commit_id, change)
                    for row, commit in zip(commits, record.commits)
                    for change in commit.changes)
    return repository


def _file_change(commit_id: int, record: ChangeRecord) -> FileChange:
    """Return one file_change row (FR-50) with the package-filter answer."""
    change = record.change
    return FileChange(
        commit_id=commit_id, change_type=change.change_type.value,
        old_path=change.old.path if change.old else None,
        new_path=change.new.path if change.new else None,
        old_blob=change.old.blob if change.old else None,
        new_blob=change.new.blob if change.new else None,
        rename_similarity=change.similarity, is_package=record.is_package,
        lines_added=change.lines_added, lines_removed=change.lines_removed,
        hunks=canonical_json([[hunk.old_start, hunk.old_length, hunk.new_start,
                               hunk.new_length] for hunk in change.hunks]))


def _select_commits(session: Session, repository_id: int, unit_id: int,
                    shas: Iterable[str]) -> None:
    """Set unit_id on the unit's selected commits, which no other unit may hold."""
    shas = list(shas)
    for start in range(0, len(shas), _BATCH):
        batch = shas[start:start + _BATCH]
        result = session.execute(update(Commit).where(
            Commit.repository_id == repository_id, Commit.sha.in_(batch),
            Commit.unit_id.is_(None)).values(unit_id=unit_id))
        if result.rowcount != len(batch):
            raise RuntimeError("a selected commit is missing or belongs to another unit")


def _debt_item(unit_id: int, item: TrackedItem, commit_ids: dict[str, int]) -> DebtItem:
    """Return one debt_item row: the FR-53 record and its FR-55 tracking fields."""
    introduction, tracking = item.introduction, item.tracking
    return DebtItem(
        unit_id=unit_id, introducing_commit_id=commit_ids[introduction.commit_sha],
        item_key=introduction.key.serialised(), symbol=introduction.symbol,
        message_id=introduction.message_id, category=introduction.category,
        message=introduction.message, line=introduction.line, scope=introduction.scope,
        normalised_text=introduction.normalised_text, occurrence=introduction.occurrence,
        path=introduction.path, blob=introduction.blob,
        introduced_at=iso_utc(introduction.introduced_at),
        c1_commit_id=commit_ids[tracking.c1_sha] if tracking.c1_sha else None,
        c1_date=iso_utc(tracking.c1_date) if tracking.c1_date else None,
        time_to_event_days=tracking.time_to_event_days,
        censoring_date=iso_utc(tracking.censoring_date) if tracking.censoring_date else None,
        censoring_reason=tracking.censoring_reason, path_before_c1=tracking.path_before_c1,
        blob_before_c1=tracking.blob_before_c1, path_after_c1=tracking.path_after_c1,
        blob_after_c1=tracking.blob_after_c1, line_before_c1=tracking.line_before_c1,
        text_before_c1=tracking.text_before_c1, clock_anomaly=tracking.clock_anomaly)


def _add_outcomes(session: Session, unit_id: int, rows: Sequence[DebtItem],
                  outcome: UnitOutcome) -> None:
    """Add the FR-56 and FR-57 records of a unit's items and its summaries (Days 7, 8)."""
    for row, item in zip(rows, outcome.items, strict=True):
        found = item.disappearance
        if found is not None:
            session.add(Disappearance(
                item_id=row.item_id, bulk_a1=found.bulk_a1, bulk_a2=found.bulk_a2,
                bulk_a3=found.bulk_a3, file_removed=found.file_removed,
                parse_failure_reason=found.parse_failure_reason,
                counterpart_method=found.counterpart.method,
                counterpart_scope=found.counterpart.scope,
                similarity=found.counterpart.similarity,
                touch_exact=found.touch.exact if found.touch is not None else None,
                touch_near=found.touch.near if found.touch is not None else None,
                touch_scope=found.touch.scope if found.touch is not None else None,
                resolver_trace_status=found.resolver_trace_status,
                resolver_tool=found.resolver_tool, same_name=found.same_name,
                same_email=found.same_email))
        rate = item.base_rate
        if rate is None:
            raise RuntimeError("every debt item has a base rate (FR-56)")
        session.add(BaseRate(item_id=row.item_id, ai_traced_count=rate.ai_traced,
                             non_traced_count=rate.non_traced,
                             ai_traced_share=rate.ai_traced_share,
                             window_end=iso_utc(rate.window_end)))
        values = item.covariates
        if values is None:
            raise RuntimeError("every debt item has covariates (FR-57)")
        session.add(Covariate(
            item_id=row.item_id, file_origin=values.file_origin, volatility=values.volatility,
            file_size=values.file_size, file_age_days=values.file_age_days,
            baseline_density=values.baseline_density, commit_lines=values.commit_lines,
            commit_files=values.commit_files, month=values.month, author_id=values.author_id,
            account_tenure_days=values.account_tenure_days,
            prior_commits_to_file=values.prior_commits_to_file,
            later_activity=values.later_activity))
    session.flush()  # disappearance rows exist before the item modes that reference them
    for row, item in zip(rows, outcome.items, strict=True):
        if item.disappearance is not None:
            session.add_all(ItemMode(item_id=row.item_id, tau=tau, mode=mode,
                                     local_fix=mode == LOCAL_FIX, unvalidated=True)
                            for tau, mode in item.disappearance.modes)
    session.add_all(UnitSummary(unit_id=unit_id, tau=summary.tau,
                                disappearances=summary.disappearances,
                                local_fixes=summary.local_fixes,
                                local_fix_share=summary.local_fix_share)
                    for summary in outcome.summaries)
    session.flush()
