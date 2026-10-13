"""ResearchService: the research run lifecycle facade the command line calls (FR-48).

The command line talks to the research engine only through this service (Freeze
section 2), and this layer never imports the git_handler (NFR-14).

validate() reads and checks a run file and returns it, with no side effects: no
connection, no clone and no file (not the research store, the lint cache, the key file
or a lock).

run() follows step 2 of the RG1 pipeline (Freeze section 8) in this order, and nothing
is cloned, locked or written before the checks that precede it pass:

1. validate the run file (exit 1);
2. check Python 3.11 and the pinned tools (NFR-24, exit 3);
3. refuse while CL-03 is incomplete (Task 7 Day 6 decision 1, exit 3), before any lock,
   clone or write;
4. refuse a stored run whose completed units lack required records (Task 7 Day 8,
   decisions R3.2, R3.3 and A: exit 1 placeholder), reading only: when the key file and
   research.db both exist, the run identifier is computed from the existing key and the
   store is read in SQLite's read-only mode, so a refused run leaves no lock, key file,
   lint cache, clone-folder change or journal behind. Whatever this read cannot settle
   (no key file or store yet, a store another process is writing or that needs recovery,
   a file of another kind) is left to the steps below, which report it as before;
5. take the CON-03 lock (exit 3 when held, OI-04: rejected, never queued);
6. load the key file, creating it on first use (NFR-27), and compute the run identifier
   (FR-48), which is reported before anything else;
7. open research.db and check the stored run again under the lock, so a change made by
   another process after step 4 is still caught; only then delete any clone a killed run
   left in the run clone folder, ``clones`` inside the lock's per-user folder, which only
   the lock holder uses (FR-10), open the lint cache and let ResearchJob start or resume
   the run, with its clones in the run clone folder.

The store path is read relative to the run file's folder (Day 6 reading), as is a
relative key_file; ``~`` is expanded. The optional PAT is passed to the accessibility
check only: it is never stored, logged or put into an error message (FR-04, NFR-19).

status() lists every run in the run file's research store with its units, read-only:
it never creates the store, a lock, the key file or the lint cache, and it does not
need the key (Day 6 reading). Units without a row are listed as planned, from each run's
canonical run file.

Failures reach the command line as RunFileInvalid (exit 1) or ResearchRunError, which
carries the three FR-05 parts and the exit code when one is agreed: 1 for SHAs that
cannot be selected, 3 for the refusals above and for a changed history (FR-49). Every
other failure has exit_code None and names its category and the open item blocking its
code (Day 6 decision 3): CL-15 for a clone, network, disk or file failure (SRS v2.3 exit 4;
the Specification Freeze has codes 0 to 3 only), CL-19 for a failed accessibility check at
run time, and none for an unusable key file, a store or lint cache of another kind, a
history read failure, tracking stopped on a binary diff or, from Task 7 Day 8, a stored
run whose completed units lack required records (StoreIncomplete, decision R4.3). None of
them is invalid input.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import DatabaseError, OperationalError

from tdea import __version__
from tdea.aggregator.key_store import DEFAULT_KEY_FILE, KeyFileError, KeyStore
from tdea.aggregator.lint_cache import DEFAULT_LINT_CACHE, LintCache
from tdea.aggregator.lock import AnalysisLock, LockUnavailable, default_lock_directory
from tdea.aggregator.research_job import (COMPLETE, PLANNED, HistoryMismatch, ResearchJob,
                                          RunContext, RunObserver, RunResult, StoreIncomplete,
                                          UnitCounts, check_store)
from tdea.aggregator.research_models import Repository, Run, Unit, iso_utc
from tdea.aggregator.research_store import ResearchStore
from tdea.aggregator.run_file import RunFile, read_run_file
from tdea.aggregator.run_identity import canonical_text, run_identifier
from tdea.analyzer.research.engine import (CL15, HistoryChanged, ResearchEngine,
                                           RulesIncomplete, SelectionInvalid, UnitStopped)
from tdea.analyzer.research.profile import EnvironmentFacts, EnvironmentRefused

_AGREED = (EnvironmentRefused, RulesIncomplete, LockUnavailable, HistoryChanged,
           HistoryMismatch, SelectionInvalid, UnitStopped, StoreIncomplete)
CLONE_FOLDER = "clones"
ANOTHER_KIND = "store or lint cache of another kind"
_Opened = TypeVar("_Opened", bound=AbstractContextManager)


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


class ResearchRunError(Exception):
    """A run or status refused or stopped: three FR-05 parts and the exit code, if agreed.

    When no exit code is agreed (``exit_code`` None), ``category`` names the kind of
    failure and ``open_item`` the open decision blocking its code (CL-15, CL-19), or None
    when no source defines a code for it.
    """

    def __init__(self, error_type: str, cause: str, action: str, exit_code: int | None,
                 category: str | None = None, open_item: str | None = None) -> None:
        """Keep the three parts, the agreed exit code (or None) and the failure category."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action
        self.exit_code = exit_code
        self.category = category
        self.open_item = open_item

    @classmethod
    def wrap(cls, error: Exception) -> ResearchRunError:
        """Return the error for a structured lower-layer failure, keeping its exit code."""
        return cls(error.error_type, error.cause, error.action,  # type: ignore[attr-defined]
                   getattr(error, "exit_code", None), getattr(error, "category", None),
                   getattr(error, "open_item", None))


@dataclass(frozen=True)
class ServicePaths:
    """Where the lock, the lint cache and the default key file live."""

    lock_directory: Path | None = None  # None: the per-user TDEA temporary folder
    lint_cache: Path = DEFAULT_LINT_CACHE
    key_file: Path = DEFAULT_KEY_FILE  # used when the run file names none


@dataclass(frozen=True)
class UnitStatus:
    """One unit as status lists it."""

    repository: str
    window_start: datetime
    window_end: datetime
    status: str
    completed_at: str | None = None
    counts: UnitCounts | None = None


@dataclass(frozen=True)
class RunStatus:
    """One run and its units."""

    run_id: str
    kind: str
    status: str
    started_at: str | None
    ended_at: str | None
    units: tuple[UnitStatus, ...] = ()

    @property
    def complete_units(self) -> int:
        """Return how many units are complete."""
        return sum(1 for unit in self.units if unit.status == COMPLETE)


@dataclass(frozen=True)
class StoreStatus:
    """What status found in one research store."""

    store: Path
    exists: bool
    runs: tuple[RunStatus, ...] = field(default_factory=tuple)


class ResearchService:
    """Validates run files, starts or resumes RG1 runs, and lists runs."""

    def __init__(self, engine: ResearchEngine | None = None,
                 clock: Callable[[], datetime] = _utc_now, *,
                 paths: ServicePaths | None = None,
                 environment: EnvironmentFacts | None = None) -> None:
        """Use ``engine`` below, ``clock`` for times, ``paths`` for local files.

        ``environment`` replaces the facts of the running interpreter for the NFR-24
        check; tests use it, real runs leave it None.
        """
        self._engine = engine if engine is not None else ResearchEngine()
        self._clock = clock
        self._paths = paths if paths is not None else ServicePaths()
        self._environment = environment

    def validate(self, path: Path | str) -> RunFile:
        """Return the validated run file at ``path``, or raise RunFileInvalid (FR-48)."""
        return read_run_file(path, self._engine, self._clock())

    def run(self, path: Path | str, token: str | None = None,
            observer: RunObserver | None = None) -> RunResult:
        """Start, or resume, the run the run file at ``path`` defines (FR-48, NFR-25)."""
        observer = observer if observer is not None else RunObserver()
        run_file = self.validate(path)
        try:
            facts = self._engine.check_environment(self._environment)
            self._engine.check_rules()
            self._refuse_incomplete_store(Path(path), run_file)
            with AnalysisLock(self._paths.lock_directory):
                return self._run_locked(Path(path), run_file, facts, token, observer)
        except _AGREED as exc:
            raise ResearchRunError.wrap(exc) from None
        except KeyFileError as exc:
            raise ResearchRunError("Key file unusable", str(exc),
                                   "Restore the key file from your backup; TDEA never "
                                   "replaces an existing key file.", None,
                                   "unusable key file") from None
        except (OSError, OperationalError):
            raise ResearchRunError(
                "Run stopped", "A file or database operation failed, for example because "
                "the disk is full or a folder cannot be written.",
                "Fix the problem and run the same run file again; completed units are "
                "kept and the run resumes.", None, "disk or file failure", CL15) from None

    def status(self, path: Path | str) -> StoreStatus:
        """List every run in the run file's research store, without writing anything."""
        store_path = _beside(Path(path), self.validate(path).store)
        if not store_path.exists():
            return StoreStatus(store_path, False)
        try:
            store = ResearchStore.read_only(store_path)
        except ValueError as exc:
            raise ResearchRunError("Not a research store", str(exc),
                                   "Correct the run file's store path.", None,
                                   ANOTHER_KIND) from None
        with store:
            return StoreStatus(store_path, True, _runs(store))

    def _refuse_incomplete_store(self, path: Path, run_file: RunFile) -> None:
        """Step 4: raise StoreIncomplete for a stored run lacking records, writing nothing.

        No lock is taken, so this read can only refuse: a store written by the current
        build adds each unit with all its records in one transaction, so a read that sees
        records missing is right whenever it happens. A read that cannot be completed is
        not a refusal; the check under the lock (step 7) decides then.
        """
        store_path = _beside(path, run_file.store)
        if not store_path.is_file():
            return  # no store yet: no stored run
        try:
            fingerprint = KeyStore(self._key_path(path, run_file)).existing_fingerprint()
            if fingerprint is None:
                return  # no key file yet: a new key gives a new run identifier
            run_id = run_identifier(run_file, self._engine.profile_hash(), __version__,
                                    fingerprint)
            with ResearchStore.read_only(store_path) as store:
                check_store(store, run_id)
        except (KeyFileError, ValueError, OSError, DatabaseError):
            return  # not settled here: the steps under the lock report it as before

    def _run_locked(self, path: Path, run_file: RunFile, facts: EnvironmentFacts,
                    token: str | None, observer: RunObserver) -> RunResult:
        """Run steps 6 and 7 with the lock held."""
        key_store = KeyStore(self._key_path(path, run_file))
        key, fingerprint = key_store.key(), key_store.fingerprint()
        run_id = run_identifier(run_file, self._engine.profile_hash(), __version__,
                                fingerprint)
        observer.run_identified(run_id)
        metadata = self._engine.run_metadata(facts)
        with _open(ResearchStore, _beside(path, run_file.store), "research store") as store:
            check_store(store, run_id)  # before the clone folder or the lint cache is touched
            clones = self._clone_folder()
            self._engine.discard_stale_clones(clones)
            with _open(LintCache, self._paths.lint_cache, "lint cache") as cache:
                context = RunContext(run_id, run_file, canonical_text(run_file), __version__,
                                     key, fingerprint, metadata, lint_cache=cache,
                                     token=token, clone_root=clones)
                return ResearchJob(store, self._engine, self._clock, observer).run(context)

    def _key_path(self, path: Path, run_file: RunFile) -> Path:
        """Return the key file: the run file's key_file beside it, else the default."""
        return (_beside(path, run_file.key_file) if run_file.key_file is not None
                else self._paths.key_file)

    def _clone_folder(self) -> Path:
        """Return the run clone folder: ``clones`` in the lock's per-user folder."""
        folder = self._paths.lock_directory
        return (folder if folder is not None else default_lock_directory()) / CLONE_FOLDER


def _open(factory: Callable[[Path], _Opened], path: Path, what: str) -> _Opened:
    """Open research.db or lintcache.db; a file of another kind is refused in three parts.

    A file that is not SQLite, or that holds other tables, is never written. A failure to
    open or write the file at all (OperationalError, OSError) is left to the caller.
    """
    try:
        return factory(path)
    except OperationalError:
        raise
    except (ValueError, DatabaseError):
        raise ResearchRunError(
            f"Not a {what}", f"{path} cannot be used as the {what}: it is not a SQLite "
            "database, or it holds tables of another kind. TDEA has not changed it.",
            f"Move that file away, or point TDEA at another {what}.", None,
            ANOTHER_KIND) from None


def _beside(run_file: Path, value: str) -> Path:
    """Return a path from the run file, relative to the run file's folder; ~ is expanded."""
    return run_file.expanduser().resolve().parent / Path(value).expanduser()


def _runs(store: ResearchStore) -> tuple[RunStatus, ...]:
    """Read every run and its units."""
    with store.reader() as session:
        runs = session.scalars(select(Run).order_by(Run.started_at, Run.run_id)).all()
        rows = session.execute(select(Repository.url, Unit).join(
            Repository, Unit.repository_id == Repository.repository_id)).all()
    by_run: dict[str, dict[tuple[str, str], Unit]] = {}
    for url, unit in rows:
        by_run.setdefault(unit.run_id, {})[(url, unit.window_start)] = unit
    return tuple(RunStatus(run.run_id, run.kind, run.status, run.started_at, run.ended_at,
                           _units(run, by_run.get(run.run_id, {}))) for run in runs)


def _units(run: Run, stored: dict[tuple[str, str], Unit]) -> tuple[UnitStatus, ...]:
    """Return the run's planned units from its canonical run file, with stored results."""
    planned = []
    for repository in json.loads(run.canonical_run_file).get("repositories", []):
        for window in repository["windows"]:
            start, end = _instant(window["start"]), _instant(window["end"])
            unit = stored.pop((repository["url"], iso_utc(start)), None)
            planned.append(_unit_status(repository["url"], start, end, unit))
    planned += [_unit_status(url, _instant(unit.window_start), _instant(unit.window_end), unit)
                for (url, _start), unit in stored.items()]
    return tuple(planned)


def _unit_status(url: str, start: datetime, end: datetime, unit: Unit | None) -> UnitStatus:
    """Return one unit's status: planned without a row, else the row's."""
    if unit is None:
        return UnitStatus(url, start, end, PLANNED)
    return UnitStatus(url, start, end, unit.status, unit.completed_at, UnitCounts(
        unit.selected_commits, unit.introductions, unit.lint_failures,
        unit.commits_after_cutoff))


def _instant(text: str) -> datetime:
    """Parse a stored UTC instant (ending in Z or +00:00) on Python 3.10 to 3.12."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
