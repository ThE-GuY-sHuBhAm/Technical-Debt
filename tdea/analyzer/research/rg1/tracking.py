"""ItemTracker: forward tracking and censoring of introduced items (FR-55, RE-10).

For each introduced item, the later first-parent commits that touch the item's file are
examined in order, following renames, and c1 is the first whose version no longer
contains a match for the item under the FR-53 rule (ItemMatcher: the unchanged-line
mapping of that commit's hunks first, then line order). A commit that deletes the file,
or whose version of it fails to lint, ends the match there (FR-55; the mode is FR-56's).
Following ends at the first commit dated after the end of W (introduction date + W
calendar months) or after the cut-off; an item without c1 by then is censored with
reason "window" or "cut-off", and its censoring date is the earlier of the two. The scan
is linear, which FR-55 names the reference; there is no bisection (Freeze section 12).

Recorded (FR-55): introduction date; c1 SHA and date; time-to-event = c1 date minus
introduction date in days to 4 decimals (committer dates, UTC), kept when negative and
then flagged as a clock anomaly; censoring date and reason; path and blob before and
after c1; the warning's line and text before c1.

Readings where the frozen texts are silent (Task 7 Day 6 report; each needs approval):

* End of W, month end: adding W calendar months keeps the day of the month and the time
  of day, and a day the target month lacks becomes that month's last day (31 August + 6
  months = 28 February, or 29 in a leap year). FR-55 says only "introduction date + W
  calendar months".
* Rounding to 4 decimals: the exact interval is rounded half to even. FR-55 says only "in
  days to 4 decimals". A tie needs an interval of an odd multiple of 108 seconds.
* Which commit ends following: the first later first-parent commit dated after the end
  of W or after the cut-off, whether or not it touches the file. Without clock skew this
  is also the first such touching commit; with skew the two readings can differ. A commit
  dated after the cut-off is not in the history (FR-49), so the first missing position
  in the chain ends following too.
* Reason on a tie: "cut-off" applies when the cut-off comes before the end of W (RE-10:
  "C persists to the cut-off before W ends"); when the two coincide the reason is
  "window".
* Text before c1: the flagged source line as Python splits the file, without its line
  ending and not normalised; the normalised text is already the item key's.
* A commit diffed as binary on the tracked file has no hunks, so the FR-53 mapping cannot
  be applied to it: tracking stops with TrackingStopped (Day 6 decision on binary diffs),
  unless the version at that commit is deleted or fails to lint, which end the match
  without hunks.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Protocol

from tdea.analyzer.research.profile import OK, LintResult
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import (ItemMatcher, Occurrence, occurrences,
                                                 source_lines)
from tdea.git_handler.file_changes import FileChange
from tdea.git_handler.history_reader import FirstParentHistory

WINDOW = "window"
CUT_OFF = "cut-off"
DECIMALS = 4
_DAY = timedelta(days=1)


class TrackingSource(Protocol):
    """What tracking reads: a commit's file changes, a blob, and a version's lint result."""

    def file_changes(self, sha: str) -> Sequence[FileChange]:
        """Return the commit's file changes against its first parent (FR-50)."""

    def read_blob(self, blob: str) -> bytes:
        """Return a blob's exact bytes."""

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Return the research-profile lint result of one file version (FR-51)."""


class TrackingStopped(Exception):
    """Tracking cannot apply FR-53 at a commit; carries the three FR-05 parts."""

    def __init__(self, cause: str) -> None:
        """Keep the three parts; the cause names the commit and the file."""
        self.error_type = "Item tracking stopped"
        self.cause = cause
        self.action = ("Check the repository's .gitattributes for that file; the unit "
                       "cannot be completed while Git diffs it as binary.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


@dataclass(frozen=True)
class ChainCommit:
    """One position of the first-parent chain: a commit, or one left out by the cut-off."""

    sha: str
    position: int
    committed_at: datetime
    after_cutoff: bool = False


@dataclass(frozen=True)
class Tracking:
    """The FR-55 tracking fields of one item: either c1 or censoring."""

    introduced_at: datetime
    c1_sha: str | None = None
    c1_date: datetime | None = None
    time_to_event_days: float | None = None
    censoring_date: datetime | None = None
    censoring_reason: str | None = None
    path_before_c1: str | None = None
    blob_before_c1: str | None = None
    path_after_c1: str | None = None
    blob_after_c1: str | None = None
    line_before_c1: int | None = None
    text_before_c1: str | None = None
    clock_anomaly: bool = False


@dataclass(frozen=True)
class _Version:
    """The item's file at one commit: path, blob, content, occurrences and the item."""

    path: str
    blob: str
    content: bytes
    occurrences: tuple[Occurrence, ...]
    item: Occurrence


def chain_of(history: FirstParentHistory) -> tuple[ChainCommit, ...]:
    """Return every position of the chain, the cut-off's exclusions included, in order."""
    entries = [ChainCommit(commit.sha, commit.position, commit.committed_at)
               for commit in history.commits]
    entries += [ChainCommit(excluded.sha, excluded.position, excluded.committed_at, True)
                for excluded in history.excluded]
    return tuple(sorted(entries, key=lambda entry: entry.position))


def add_calendar_months(moment: datetime, months: int) -> datetime:
    """Return ``moment`` + ``months`` calendar months, a missing day clamped to month end."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    return moment.replace(year=year, month=month,
                          day=min(moment.day, calendar.monthrange(year, month)[1]))


def days_between(start: datetime, end: datetime) -> float:
    """Return end - start in days, rounded half to even to 4 decimals, from the exact value."""
    rounded = round(_exact_days(end - start) * 10 ** DECIMALS)
    return float(Fraction(rounded, 10 ** DECIMALS)) + 0.0


def _exact_days(interval: timedelta) -> Fraction:
    """Return an interval in days as an exact fraction."""
    return Fraction(interval // timedelta(microseconds=1), _DAY // timedelta(microseconds=1))


class ItemTracker:
    """Applies FR-55 to one introduced item with a linear scan."""

    def __init__(self, source: TrackingSource, matcher: ItemMatcher | None = None) -> None:
        """Read through ``source``; match with ``matcher`` (D-12)."""
        self._source = source
        self._matcher = matcher if matcher is not None else ItemMatcher()

    def track(self, introduction: Introduction, chain: Sequence[ChainCommit],
              w_months: int, cutoff: datetime) -> Tracking:
        """Follow ``introduction`` through ``chain`` to c1 or to censoring."""
        if w_months < 1:
            raise ValueError("W must be at least one month")
        start = _position_of(chain, introduction.commit_sha)
        end_of_w = add_calendar_months(introduction.introduced_at, w_months)
        version = self._first_version(introduction)
        for entry in chain[start + 1:]:
            if entry.after_cutoff or entry.committed_at > end_of_w \
                    or entry.committed_at > cutoff:
                break
            change = _change_of(self._source.file_changes(entry.sha), version.path)
            if change is None:
                continue
            if change.old.blob != version.blob:
                raise RuntimeError(f"history out of order at {entry.sha}: "
                                   f"{version.path} is not the version tracked")
            following = self._next_version(entry, change, version)
            if following is None:
                return _disappeared(introduction, entry, change, version)
            version = following
        reason = WINDOW if end_of_w <= cutoff else CUT_OFF
        return Tracking(introduction.introduced_at, censoring_date=min(end_of_w, cutoff),
                        censoring_reason=reason)

    def _first_version(self, introduction: Introduction) -> _Version:
        """Return the item's file at its introducing commit, with the item found in it."""
        content = self._source.read_blob(introduction.blob)
        result = self._source.lint(content, introduction.path, introduction.blob)
        if result.status != OK:
            raise RuntimeError("an introduced item's version must lint")
        found = occurrences(result.messages, content)
        item = next((occurrence for occurrence in found
                     if occurrence.key == introduction.key
                     and occurrence.index == introduction.occurrence), None)
        if item is None:
            raise RuntimeError("the introduced item is not in its own version")
        return _Version(introduction.path, introduction.blob, content, found, item)

    def _next_version(self, entry: ChainCommit, change: FileChange,
                      version: _Version) -> _Version | None:
        """Return the item's version at ``entry``, or None if the match ends there."""
        if change.new is None:
            return None  # the file is deleted
        content = self._source.read_blob(change.new.blob)
        result = self._source.lint(content, change.new.path, change.new.blob)
        if result.status != OK:
            return None  # a file that fails to lint ends the match (FR-55)
        if change.binary:
            raise TrackingStopped(
                f"Commit {entry.sha} changes {version.path}, which Git diffs as binary, so "
                "it has no hunks and the FR-53 match of a tracked item cannot be made.")
        found = occurrences(result.messages, content)
        result_pairs = self._matcher.match(version.occurrences, found, change.hunks)
        partner = next((new for old, new in result_pairs.matched if old is version.item),
                       None)
        if partner is None:
            return None
        return _Version(change.new.path, change.new.blob, content, found, partner)


def _position_of(chain: Sequence[ChainCommit], sha: str) -> int:
    """Return the index of ``sha`` in the chain."""
    for index, entry in enumerate(chain):
        if entry.sha == sha and not entry.after_cutoff:
            return index
    raise ValueError(f"{sha} is not in the history")


def _change_of(changes: Sequence[FileChange], path: str) -> FileChange | None:
    """Return the change whose old side is the tracked path, or None if it is untouched."""
    found = [change for change in changes if change.old is not None and change.old.path == path]
    if len(found) > 1:
        raise RuntimeError(f"{path} has more than one change in one commit")
    return found[0] if found else None


def _disappeared(introduction: Introduction, entry: ChainCommit, change: FileChange,
                 version: _Version) -> Tracking:
    """Return the tracking of an item whose match ends at ``entry`` (c1)."""
    lines = source_lines(version.content)
    line = version.item.line
    interval = entry.committed_at - introduction.introduced_at
    return Tracking(
        introduction.introduced_at,
        c1_sha=entry.sha,
        c1_date=entry.committed_at,
        time_to_event_days=days_between(introduction.introduced_at, entry.committed_at),
        path_before_c1=version.path,
        blob_before_c1=version.blob,
        path_after_c1=change.new.path if change.new is not None else None,
        blob_after_c1=change.new.blob if change.new is not None else None,
        line_before_c1=line,
        text_before_c1=lines[line - 1] if 1 <= line <= len(lines) else "",
        clock_anomaly=interval < timedelta(0),
    )
