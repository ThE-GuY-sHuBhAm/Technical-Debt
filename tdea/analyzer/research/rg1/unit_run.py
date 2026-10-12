"""One RG1 repository and its units: steps 4 to 10 of the RG1 pipeline (Freeze section 8).

RepositorySession acquires one repository and reads what all its units share: the
accessibility check and optional PAT (FR-03, FR-04), a full clone (FR-06), the clone
record or the recorded one when a run resumes (FR-49), the first-parent history up to the
cut-off, each commit's provenance and identity keys (FR-54, NFR-27) and its file changes
(FR-50), with the package filter applied to each changed path (FR-51 d). Raw identities
stay in memory here; only identity keys and rewritten messages leave (CommitRecord).

run_unit() is one unit (one repository-window, NFR-25): the selected commits (FR-49);
for each, the parent and new versions of its changed package .py files, linted alone
through the lint cache (FR-51, NFR-25); introductions (FR-53, IntroductionDetector); and
the tracking of every introduced item (FR-55, ItemTracker); for every item that
disappears, its mode at each tau with all signals (FR-56, DisappearanceClassifier); every
item's base rate (FR-56, base_rate); and the unit's summary at each tau (Task 7 Day 7).
Covariate records (FR-57) follow on Day 8. Nothing is written here: the aggregator
stores a unit's outcome in one transaction, and a fail-closed stop of FR-56 is raised
before it, so nothing of that unit is stored.

Run control readings (Task 7 Day 6 report):

* The HEAD the history is read from is recorded: the pinned HEAD when the run file pins
  one, else the HEAD at clone; a resumed repository reads from its recorded HEAD and
  cut-off. A fresh clone that lacks that HEAD is refused as "history changed" (FR-49,
  exit 3), as is a pinned HEAD the first clone lacks (execution plan, Day 6 exit
  condition).
* Listed SHAs outside their window or absent from the history are checked for every
  window of a repository right after its history is read, before any of its units is
  analysed (FR-48, exit 1).
* Only package .py files are linted in a unit (RE-05: non-package files produce no
  messages), and lint failures are counted for them alone. A changed .py file that Git
  diffs as binary has no hunks, so by FR-53 it introduces nothing; it is listed.
* A unit's "commits after the cut-off" are the first-parent commits dated after the
  cut-off whose committer date lies in the unit's window.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from tdea.analyzer.research.profile import LintCachePort, LintResult, ResearchProfile
from tdea.analyzer.research.rg1.base_rate import BaseRate, base_rate
from tdea.analyzer.research.rg1.bulk import BulkRules
from tdea.analyzer.research.rg1.disappearance import (Disappearance, DisappearanceClassifier,
                                                      UnitSummary, unit_summaries)
from tdea.analyzer.research.rg1.fail_closed import provenance_unknown
from tdea.analyzer.research.rg1.introduction import (FileVersions, Introduction,
                                                     IntroductionDetector)
from tdea.analyzer.research.rg1.package_filter import PYTHON_SUFFIX
from tdea.analyzer.research.rg1.provenance import ListVersion, Provenance, TraceStatus
from tdea.analyzer.research.rg1.pseudonymiser import Pseudonymiser
from tdea.analyzer.research.rg1.tracking import ChainCommit, ItemTracker, Tracking, chain_of
from tdea.git_handler.commit_selector import CommitSelector, Window
from tdea.git_handler.errors import TdeaError
from tdea.git_handler.file_changes import FileChange
from tdea.git_handler.history_reader import FirstParentHistory, HistoryReader
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit
from tdea.git_handler.url_validator import RepositoryRef


class Classifier(Protocol):
    """FR-54 provenance (ProvenanceClassifier, or a test double)."""

    def classify(self, raw: RawCommitStrings) -> Provenance:
        """Return the provenance of one commit from its raw strings."""

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the versions of the lists used."""


class PackageRules(Protocol):
    """FR-51 (d) package filter (PackageFilter, or a test double)."""

    def is_package(self, path: str) -> bool:
        """Return True for package code."""

    def list_version(self) -> ListVersion:
        """Return the version of the list used."""


class RepositoryAccess(Protocol):
    """FR-03 accessibility check and the address FR-06 clones from."""

    def check(self, repository: RepositoryRef, token: str | None) -> None:
        """Raise TdeaError unless the repository is public and non-empty."""

    def clone_source(self, repository: RepositoryRef) -> RepositoryRef | Path:
        """Return what LocalRepository.clone() is given for the repository."""


class HistoryChanged(Exception):
    """A fresh clone lacks the HEAD the run must read from (FR-49, exit 3)."""

    exit_code = 3

    def __init__(self, url: str, head: str, recorded: bool) -> None:
        """Name the repository and the missing HEAD."""
        where = "recorded when this run started" if recorded else "pinned in the run file"
        self.error_type = "History changed"
        self.cause = (f"A fresh clone of {url} does not contain commit {head}, the HEAD "
                      f"{where}, so the same history cannot be read again (FR-49).")
        self.action = ("Restore the repository's history (for example from an archived "
                       "bundle), or start a new run with another run file.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


class SelectionInvalid(Exception):
    """A window's SHA list names commits outside it or absent from the history (exit 1)."""

    exit_code = 1

    def __init__(self, url: str, error: TdeaError) -> None:
        """Keep the selector's three parts, naming the repository."""
        self.error_type = error.error_type
        self.cause = f"{url}: {error.cause}"
        self.action = error.action
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


@dataclass(frozen=True)
class RecordedRepository:
    """What research.db holds for a repository whose first unit committed."""

    head_sha: str
    cutoff: datetime
    clone_time: datetime
    default_branch: str


@dataclass(frozen=True)
class RepositoryPlan:
    """What the run file and research.db say about reading one repository."""

    url: str
    pinned_head: str | None = None
    cutoff: datetime | None = None  # the run file's cut-off; None means the clone time
    recorded: RecordedRepository | None = None  # set when the repository already started


@dataclass(frozen=True)
class ChangeRecord:
    """One file change of a commit, with the package-filter answer for its path."""

    change: FileChange
    is_package: bool


@dataclass(frozen=True)
class CommitRecord:
    """One first-parent commit as stored: identity keys only, never raw strings (NFR-27)."""

    sha: str
    position: int
    parents: tuple[str, ...]
    is_merge: bool
    authored_at: datetime
    committed_at: datetime
    author_name_key: str | None
    author_email_key: str | None
    committer_name_key: str | None
    committer_email_key: str | None
    message: str
    provenance: Provenance
    changes: tuple[ChangeRecord, ...]


@dataclass(frozen=True)
class RepositoryRecord:
    """The FR-49 repository record and its history, written with its first unit."""

    url: str
    default_branch: str
    clone_time: datetime
    head_sha: str
    cutoff: datetime
    commits: tuple[CommitRecord, ...]


@dataclass(frozen=True)
class UnitWindow:
    """A unit's committer-date window [start, end) and optional SHA list (UTC)."""

    start: datetime
    end: datetime
    commits: tuple[str, ...] | None = None


@dataclass(frozen=True)
class TrackedItem:
    """One introduced item, its FR-55 tracking and its FR-56 disappearance and base rate."""

    introduction: Introduction
    tracking: Tracking
    disappearance: Disappearance | None = None  # None while the item has no c1
    base_rate: BaseRate | None = None


@dataclass(frozen=True)
class UnitOutcome:
    """Everything one unit's transaction stores, and what it listed."""

    selected: tuple[str, ...]
    items: tuple[TrackedItem, ...]
    lint_failures: int
    commits_after_cutoff: int
    binary_files: tuple[tuple[str, str], ...] = ()  # (commit SHA, path), listed only
    summaries: tuple[UnitSummary, ...] = ()  # one per tau (FR-56)


class HistorySource:
    """TrackingSource over one clone: memoised file changes, blobs and cached lint."""

    def __init__(self, reader: HistoryReader, profile: ResearchProfile,
                 cache: LintCachePort | None) -> None:
        """Read through ``reader``; lint with ``profile`` through ``cache``."""
        self._reader = reader
        self._profile = profile
        self._cache = cache
        self._changes: dict[str, tuple[FileChange, ...]] = {}
        self._lints: dict[tuple[str, str], LintResult] = {}

    def file_changes(self, sha: str) -> tuple[FileChange, ...]:
        """Return a commit's file changes, read once."""
        if sha not in self._changes:
            self._changes[sha] = self._reader.file_changes(sha)
        return self._changes[sha]

    def read_blob(self, blob: str) -> bytes:
        """Return a blob's bytes from the object database."""
        return self._reader.read_blob(blob)

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Lint one version under its own file name, once per blob and name."""
        name = PurePosixPath(path).name
        if (blob, name) not in self._lints:
            self._lints[(blob, name)] = self._profile.lint(content, name, blob_sha=blob,
                                                           cache=self._cache)
        return self._lints[(blob, name)]


@dataclass(frozen=True)
class SessionParts:
    """What a RepositorySession is built from (engine internals)."""

    local: LocalRepository
    source: HistorySource
    history: FirstParentHistory
    record: RepositoryRecord
    statuses: Mapping[str, Provenance]


class RepositorySession:
    """One cloned repository of a run, its history and its units; close() deletes it."""

    def __init__(self, parts: SessionParts, package_rules: PackageRules,
                 bulk_rules: BulkRules) -> None:
        """Hold the clone, its history and the record built from them."""
        self._local = parts.local
        self._history = parts.history
        self.record = parts.record
        self._statuses = parts.statuses
        self._package_rules = package_rules
        self._bulk_rules = bulk_rules
        self._source = parts.source
        self._chain: tuple[ChainCommit, ...] = chain_of(parts.history)
        self._package = {commit.sha: {_path(record.change): record.is_package
                                      for record in commit.changes}
                         for commit in parts.record.commits}

    @property
    def history_shas(self) -> tuple[str, ...]:
        """Return the history's SHAs, root to HEAD."""
        return tuple(commit.sha for commit in self._history.commits)

    def check_selections(self, windows: Sequence[UnitWindow]) -> None:
        """Raise SelectionInvalid if any window's SHA list cannot be selected (FR-48)."""
        for window in windows:
            self._select(window)

    def run_unit(self, window: UnitWindow, w_months: int) -> UnitOutcome:
        """Analyse one unit: selection, lint, introductions, tracking, modes, base rates.

        A commit of the history without a known trace status stops the unit first (fail
        closed, Day 7 reading R5): the history is stored with a repository's first unit,
        and an unknown status is never read as non-traced.
        """
        for commit in self.record.commits:
            if not isinstance(commit.provenance.status, TraceStatus):
                raise provenance_unknown(commit.sha)
        selected = self._select(window)
        detector = IntroductionDetector(self._package_rules)
        introductions: list[Introduction] = []
        failures = 0
        binary: list[tuple[str, str]] = []
        for commit in selected:
            files = self._package_versions(commit)
            provenance = self._statuses[commit.sha]
            found = detector.detect(commit, True, provenance.status, files)
            introductions.extend(found.introductions)
            failures += len(found.failed_files)
            binary.extend((commit.sha, path) for path in found.binary_files)
        tracker = ItemTracker(self._source)
        classifier = DisappearanceClassifier(
            self._source, self._bulk_rules,
            {commit.sha: commit for commit in self.record.commits})
        items = []
        for introduction in introductions:
            tracking = tracker.track(introduction, self._chain, w_months, self.record.cutoff)
            disappearance = (classifier.classify(introduction, tracking)
                             if tracking.c1_sha is not None else None)
            items.append(TrackedItem(introduction, tracking, disappearance,
                                     base_rate(introduction, self.record.commits, w_months)))
        bounds = Window(window.start, window.end)
        after_cutoff = sum(1 for excluded in self._history.excluded
                           if bounds.contains(excluded.committed_at))
        summaries = unit_summaries([item.disappearance for item in items
                                    if item.disappearance is not None])
        return UnitOutcome(tuple(commit.sha for commit in selected), tuple(items), failures,
                           after_cutoff, tuple(binary), summaries)

    def close(self) -> None:
        """Delete the clone (FR-10); safe to call more than once."""
        self._local.cleanup()

    def _select(self, window: UnitWindow) -> tuple[ResearchCommit, ...]:
        """Return the window's selected commits, or raise SelectionInvalid."""
        try:
            return CommitSelector(self._history.commits).select(
                Window(window.start, window.end, window.commits))
        except TdeaError as exc:
            raise SelectionInvalid(self.record.url, exc) from None

    def _package_versions(self, commit: ResearchCommit) -> tuple[FileVersions, ...]:
        """Return both linted versions of each changed package .py file of ``commit``."""
        package = self._package[commit.sha]
        files = []
        for change in self._source.file_changes(commit.sha):
            if change.new is None or not package.get(change.new.path, False):
                continue
            before, before_content = None, b""
            if change.old is not None:
                before_content = self._source.read_blob(change.old.blob)
                before = self._source.lint(before_content, change.old.path, change.old.blob)
            after_content = self._source.read_blob(change.new.blob)
            after = self._source.lint(after_content, change.new.path, change.new.blob)
            files.append(FileVersions(change, before, after, before_content, after_content))
        return tuple(files)


def _path(change: FileChange) -> str:
    """Return the path a change is filed under: the new path, or the old one if deleted."""
    side = change.new if change.new is not None else change.old
    return side.path


def commit_record(commit: ResearchCommit, provenance: Provenance,
                  pseudonymiser: Pseudonymiser, changes: Sequence[FileChange],
                  is_package: Callable[[str], bool]) -> CommitRecord:
    """Return the stored form of one commit: keys and rewritten message, never raw strings."""
    keys = pseudonymiser.pseudonymise(commit.raw)
    return CommitRecord(
        sha=commit.sha, position=commit.position, parents=commit.parents,
        is_merge=commit.is_merge, authored_at=commit.authored_at,
        committed_at=commit.committed_at, author_name_key=keys.author_name_key,
        author_email_key=keys.author_email_key, committer_name_key=keys.committer_name_key,
        committer_email_key=keys.committer_email_key, message=keys.message,
        provenance=provenance,
        changes=tuple(ChangeRecord(change, _package_answer(change, is_package))
                      for change in changes))


def _package_answer(change: FileChange, is_package: Callable[[str], bool]) -> bool:
    """Apply the package filter to a change's path; only .py files can be package code."""
    path = _path(change)
    return path.endswith(PYTHON_SUFFIX) and is_package(path)
