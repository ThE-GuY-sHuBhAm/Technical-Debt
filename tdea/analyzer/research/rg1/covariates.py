"""CovariateExtractor: the FR-57 covariates of one debt item (Task 7 Days 7 and 8).

FR-57: "For each debt item the system shall compute: file origin; pre-introduction
volatility; file size and age; baseline warning density under the research profile;
commit size; month of introduction; author (author_id); repository; and the rewritten
commit message. As exploratory fields it shall compute account tenure ..., the author's
prior commits to the file, and the author's commits in the W window after introduction."
Definitions follow D-6 to D-9; "Author" is the author e-mail key. Repository and the
rewritten message are not covariate columns: they are reached through the item's unit
and introducing commit (Day 8 decision Q2, no schema change).

Readings, approved provisionally until G2 (22 Oct 2026); t is the introduction date (c's
committer date, UTC):

* Recorded history only (Day 8 decision B). Every walk reads the repository's recorded
  first-parent history (FR-49: the commits dated at or before the cut-off), in history
  order. A commit left out by the cut-off is never read, so it neither counts nor stops
  a walk; Day 6 tracking and the Day 7 base rate keep their own rules.
* The file's lineage (Q4, Q5, Q10). From c back to the root, the file is followed under
  its path at c and through the old path of each rename. The first commit that adds the
  followed path ends the lineage, so a file deleted and added again starts a new lineage.
  A commit that deletes the followed path, or renames it away, before its add is found,
  or a lineage without an add, leaves the origin unestablished: the unit fails closed.
* D-6 file origin: the trace status of the commit that adds the file (c itself when c
  adds it). A merge counts through its first-parent diff (D-10).
* D-7 volatility: the lineage commits before c whose committer date lies in [t - 6
  calendar months, t), bots included, divided by 6 whatever the file's age, unrounded.
  Six months back keeps the day and time, a missing day becoming the month's last.
* File age: t minus the adding commit's date, in days to 4 decimals (FR-55's rounding);
  0.0 when c adds the file; a negative value (clock skew) is kept, never clamped.
* D-8 baseline density (Day 7 P5, Day 8 Q12): the research-profile messages of the
  parent version (the old path's version when c renames the file) divided by its radon
  SLOC (PEP 263 decoding); 0.0 when SLOC is 0, as FR-15 gives "Files with SLOC = 0 ... a
  smell density of 0.0"; null when c adds the file. A parent version that radon cannot
  read, or that has no successful lint, fails closed.
* D-9 file size (Day 7 P5): the physical lines of the file at c, counted as Python
  counts them: CR LF, LF and CR each end one, and a last line without an ending is a
  line. D-9 commit size: lines added + removed, and files changed, from c's FR-50 file
  changes against its first parent; a binary file is a changed file with no lines.
* Month: "YYYY-MM" of t in UTC (Q7). author_id: c's author e-mail key (Q8).
* Prior commits to the file (Q10): the lineage commits before c with c's author e-mail
  key, the adding commit included.
* Later activity (Q11, option a): the commits after c, anywhere in the repository, with
  c's author e-mail key, up to the first commit dated after E = t + W calendar months;
  a commit dated exactly E counts, and one dated before t (clock skew) counts. Null when
  E is after the repository's cut-off, because the window is then not fully recorded.
* When c has no author e-mail key, author_id and the three exploratory fields are null.
* Account tenure is not computed: TENURE_DEFERRED. Its column is always null while the RM
  side decides Q9 (scope of "first recorded commit", meaning of "first", time reference).
  A null tenure next to a non-null author_id means "deferred", not "no author key".
"""

from __future__ import annotations

import tokenize
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from tdea.analyzer.radon_analyzer import RadonAnalyzer
from tdea.analyzer.research.profile import OK, LintResult
from tdea.analyzer.research.rg1.base_rate import ChangeEntry
from tdea.analyzer.research.rg1.defaults import VOLATILITY_MONTHS
from tdea.analyzer.research.rg1.fail_closed import (change_inconsistent, density_unmeasurable,
                                                    origin_unestablished, provenance_unknown)
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import source_lines
from tdea.analyzer.research.rg1.provenance import Provenance, TraceStatus
from tdea.analyzer.research.rg1.tracking import add_calendar_months, days_between
from tdea.git_handler.file_changes import ChangeType, FileChange

TENURE_DEFERRED = "deferred pending RM decision (Q9)"
MONTH_FORMAT = "%Y-%m"


@dataclass(frozen=True)
class CommitSize:
    """D-9 commit size: changed lines (added + removed) and changed files of c."""

    lines: int
    files: int


@dataclass(frozen=True)
class Covariates:
    """One debt item's covariate record (FR-57); the last three fields are exploratory."""

    file_origin: str
    volatility: float
    file_size: int
    file_age_days: float
    baseline_density: float | None
    commit_lines: int
    commit_files: int
    month: str
    author_id: str | None
    account_tenure_days: float | None  # always None: TENURE_DEFERRED
    prior_commits_to_file: int | None
    later_activity: int | None


class CovariateCommit(Protocol):
    """A recorded first-parent commit (CommitRecord): what the covariates read."""

    sha: str
    committed_at: datetime
    author_email_key: str | None
    provenance: Provenance
    changes: Sequence[ChangeEntry]


class VersionSource(Protocol):
    """Blobs and their cached research-profile lint (HistorySource)."""

    def read_blob(self, blob: str) -> bytes:
        """Return a blob's bytes."""

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Return the research-profile lint of one version under its file name."""


@dataclass(frozen=True)
class _Lineage:
    """The history indexes of the commits that touch the file, from its add up to c."""

    touching: tuple[int, ...]
    added_at: int
    change_at_c: FileChange


class CovariateExtractor:
    """Computes FR-57's covariates over one repository's recorded history."""

    def __init__(self, history: Sequence[CovariateCommit], source: VersionSource,
                 cutoff: datetime, w_months: int, radon: RadonAnalyzer | None = None) -> None:
        """Read ``history`` (root to HEAD) and versions through ``source``."""
        if w_months < 1:
            raise ValueError("W must be at least one month")
        self._history = tuple(history)
        self._index = {commit.sha: index for index, commit in enumerate(self._history)}
        self._source = source
        self._cutoff = cutoff
        self._w_months = w_months
        self._radon = radon if radon is not None else RadonAnalyzer()

    def extract(self, introduction: Introduction) -> Covariates:
        """Return the covariates of the item ``introduction`` introduced."""
        at_c = self._index.get(introduction.commit_sha)
        if at_c is None:
            raise ValueError(f"{introduction.commit_sha} is not in the recorded history")
        moment = introduction.introduced_at
        if moment.tzinfo is None:
            raise ValueError("the introduction date must carry its time zone")
        commit = self._history[at_c]
        lineage = self._lineage(at_c, introduction.path)
        added = self._history[lineage.added_at]
        status = added.provenance.status
        if not isinstance(status, TraceStatus):
            raise provenance_unknown(added.sha)
        before_c = [index for index in lineage.touching if index < at_c]
        start = add_calendar_months(moment, -VOLATILITY_MONTHS)
        recent = sum(1 for index in before_c
                     if start <= self._history[index].committed_at < moment)
        size = commit_size([entry.change for entry in commit.changes])
        key = commit.author_email_key
        return Covariates(
            file_origin=status.value,
            volatility=recent / VOLATILITY_MONTHS,
            file_size=file_size(self._source.read_blob(introduction.blob)),
            file_age_days=days_between(added.committed_at, moment),
            baseline_density=self._density(lineage.change_at_c, commit.sha),
            commit_lines=size.lines, commit_files=size.files,
            month=moment.astimezone(timezone.utc).strftime(MONTH_FORMAT),
            author_id=key, account_tenure_days=None,
            prior_commits_to_file=(None if key is None else sum(
                1 for index in before_c if self._history[index].author_email_key == key)),
            later_activity=None if key is None else self._later_activity(at_c, moment, key))

    def _lineage(self, at_c: int, path: str) -> _Lineage:
        """Follow the file back from c through renames to the commit that adds it."""
        item_path, sha = path, self._history[at_c].sha
        touching = []
        change_at_c = None
        for index in range(at_c, -1, -1):
            commit = self._history[index]
            entering = [entry.change for entry in commit.changes
                        if entry.change.new is not None and entry.change.new.path == path]
            if len(entering) > 1:
                raise change_inconsistent(commit.sha, path)
            if not entering:
                if index == at_c or _leaves(commit, path):
                    raise origin_unestablished(item_path, sha)
                continue
            change = entering[0]
            touching.append(index)
            if index == at_c:
                change_at_c = change
            if change.change_type is ChangeType.ADDED or change.old is None:
                return _Lineage(tuple(reversed(touching)), index, change_at_c)
            path = change.old.path
        raise origin_unestablished(item_path, sha)

    def _density(self, change: FileChange, sha: str) -> float | None:
        """Return D-8 for the parent version of c's change, or None if c adds the file."""
        if change.old is None:
            return None
        content = self._source.read_blob(change.old.blob)
        lint = self._source.lint(content, change.old.path, change.old.blob)
        if lint.status != OK:
            raise density_unmeasurable(change.old.path, sha)
        try:
            return baseline_density(len(lint.messages), content, self._radon)
        except (SyntaxError, ValueError, tokenize.TokenError):
            raise density_unmeasurable(change.old.path, sha) from None

    def _later_activity(self, at_c: int, moment: datetime, key: str) -> int | None:
        """Return the author's commits after c up to the end of W, or None past the cut-off."""
        end_of_w = add_calendar_months(moment, self._w_months)
        if end_of_w > self._cutoff:
            return None
        count = 0
        for commit in self._history[at_c + 1:]:
            if commit.committed_at > end_of_w:
                break
            if commit.author_email_key == key:
                count += 1
        return count


def _leaves(commit: CovariateCommit, path: str) -> bool:
    """Return True if the commit deletes ``path`` or renames it away."""
    return any(entry.change.old is not None and entry.change.old.path == path
               for entry in commit.changes)


def baseline_density(message_count: int, parent: bytes | None,
                     radon: RadonAnalyzer | None = None) -> float | None:
    """Return D-8: messages in the parent version per radon SLOC; None for a new file."""
    if parent is None:
        return None
    sloc = (radon if radon is not None else RadonAnalyzer()).sloc(parent)
    return 0.0 if sloc == 0 else message_count / sloc


def file_size(content: bytes) -> int:
    """Return D-9 file size: the physical lines of the file at c."""
    return len(source_lines(content))


def commit_size(changes: Sequence[FileChange]) -> CommitSize:
    """Return D-9 commit size from c's first-parent file changes (FR-50)."""
    lines = sum((change.lines_added or 0) + (change.lines_removed or 0) for change in changes)
    return CommitSize(lines, len(changes))
