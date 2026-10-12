"""The base rate of a debt item (FR-56; Task 7 Day 7 reading R5, approved provisionally).

FR-56: "base-rate counts of later non-bot commits touching the file within W, by
provenance, with the AI-traced share"; glossary, Base rate: "...touching its file within
W". Every debt item has one (Freeze section 7: debt_item 1-1 base_rate), censored or not.
The algorithm keeps four rules apart; it reads the repository's recorded first-parent
history H only (FR-49: the commits dated at or before the cut-off), in history order.

1. Cut-off eligibility. Only commits in H are read. A commit excluded by the cut-off is
   never opened, not even to decide where to stop, so the counts end at the cut-off. Day 6
   tracking instead stops at the first excluded position; the difference is accepted
   (Day 7 decision D1) and both rules stay as they are, each tested on its own.
2. Traversal stop. The commits after the introducing commit c are visited in history
   order; the visit stops before the first one whose committer date (UTC) is after the end
   of W = introduction date + W calendar months (FR-55's rule and Day 6's month-end
   reading), whether or not it touches the file. A later commit dated before the
   introduction (clock skew) is visited. History order is provisional pending G2.
3. File identity. The file starts at the item's path at c and is followed through the old
   path of each commit's changes: a rename moves it, also after c1 and through bot commits
   (provisional); a deletion is a touch and ends the visit, so a later re-addition of that
   path is never the same file. Two changes of the followed path in one commit fail closed.
4. Counting. A touching commit counts as AI-traced or non-traced by its trace status; a
   bot commit is not counted (FR-54) but still moves the file. c is never counted; c1 is
   counted when it qualifies, and counting does not stop at c1. An unknown trace status
   fails closed.

Stored: the AI-traced and non-traced counts; the AI-traced share = AI-traced / (AI-traced +
non-traced), null when both are 0; and window_end = the theoretical end of W. The cut-off
is a separate counting restriction, recorded on the repository row (FR-62's data
dictionary states it).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tdea.analyzer.research.rg1.fail_closed import change_inconsistent, provenance_unknown
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.provenance import Provenance, TraceStatus
from tdea.analyzer.research.rg1.tracking import add_calendar_months
from tdea.git_handler.file_changes import FileChange


class ChangeEntry(Protocol):
    """A stored file change (ChangeRecord)."""

    change: FileChange


class HistoryCommit(Protocol):
    """A recorded first-parent commit (CommitRecord): what the base rate reads."""

    sha: str
    committed_at: datetime
    provenance: Provenance
    changes: Sequence[ChangeEntry]


@dataclass(frozen=True)
class BaseRate:
    """One debt item's base-rate record (FR-56)."""

    ai_traced: int
    non_traced: int
    ai_traced_share: float | None
    window_end: datetime


def base_rate(introduction: Introduction, history: Sequence[HistoryCommit],
              w_months: int) -> BaseRate:
    """Return the base rate of one item over the recorded history ``history``."""
    end_of_w = add_calendar_months(introduction.introduced_at, w_months)
    position = next((index for index, commit in enumerate(history)
                     if commit.sha == introduction.commit_sha), None)
    if position is None:
        raise ValueError(f"{introduction.commit_sha} is not in the recorded history")
    path = introduction.path
    counts = {TraceStatus.AI_TRACED: 0, TraceStatus.NON_TRACED: 0}
    for commit in history[position + 1:]:
        if commit.committed_at > end_of_w:
            break
        change = _change_of(commit, path)
        if change is None:
            continue
        status = commit.provenance.status
        if not isinstance(status, TraceStatus):
            raise provenance_unknown(commit.sha)
        if status in counts:
            counts[status] += 1
        if change.new is None:
            break
        path = change.new.path
    ai_traced, non_traced = counts[TraceStatus.AI_TRACED], counts[TraceStatus.NON_TRACED]
    total = ai_traced + non_traced
    return BaseRate(ai_traced, non_traced, ai_traced / total if total else None, end_of_w)


def _change_of(commit: HistoryCommit, path: str) -> FileChange | None:
    """Return the commit's change of the followed path, or None if it does not touch it."""
    found = [entry.change for entry in commit.changes
             if entry.change.old is not None and entry.change.old.path == path]
    if len(found) > 1:
        raise change_inconsistent(commit.sha, path)
    return found[0] if found else None
