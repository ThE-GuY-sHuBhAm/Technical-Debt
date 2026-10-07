"""CommitSelector: a unit's selected commits (FR-49).

A unit's selected commits are its SHA list when one is given, otherwise every commit of
the first-parent history whose committer date lies in its window. Windows use committer
dates, start inclusive and end exclusive (SRS 4.5.2). A listed SHA that is outside its
window or absent from the history (which already leaves out commits after the cut-off)
is rejected, as FR-48 requires after the clone. Selection keeps history order (root to
HEAD) and includes merge commits (D-10). Introduction, provenance and package-file
rules are applied later, by FR-53 and FR-54.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.research_commit import ResearchCommit


@dataclass(frozen=True)
class Window:
    """A committer-date window [start, end) with an optional SHA list."""

    start: datetime
    end: datetime
    commits: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Require timezone-aware bounds with start before end."""
        for moment in (self.start, self.end):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("window bounds must be timezone-aware datetimes")
        if self.start >= self.end:
            raise ValueError("a window's start must be before its end")

    def contains(self, moment: datetime) -> bool:
        """Return True if ``moment`` lies in [start, end)."""
        return self.start <= moment.astimezone(timezone.utc) < self.end


class CommitSelector:
    """Selects one unit's commits from a first-parent history."""

    def __init__(self, history: Iterable[ResearchCommit]) -> None:
        """Select from ``history``, in root-to-HEAD order."""
        self._history = tuple(history)

    def select(self, window: Window) -> tuple[ResearchCommit, ...]:
        """Return the window's SHA list, or every commit in the window."""
        if window.commits is None:
            return tuple(commit for commit in self._history
                         if window.contains(commit.committed_at))
        wanted = set(window.commits)
        by_sha = {commit.sha: commit for commit in self._history}
        absent = sorted(sha for sha in wanted if sha not in by_sha)
        outside = sorted(sha for sha in wanted
                         if sha in by_sha and not window.contains(by_sha[sha].committed_at))
        if absent or outside:
            raise _selection_error(absent, outside)
        return tuple(commit for commit in self._history if commit.sha in wanted)


def _selection_error(absent: list[str], outside: list[str]) -> TdeaError:
    """Return the FR-48 error for SHAs that cannot be selected."""
    parts = []
    if absent:
        parts.append(f"not in the first-parent history up to the cut-off: {', '.join(absent)}")
    if outside:
        parts.append(f"outside the window: {', '.join(outside)}")
    return TdeaError(
        ErrorCode.INVALID_SELECTION,
        "Invalid commit list",
        "Listed commits are " + "; ".join(parts) + ".",
        "Correct the window's commits list in the run file.",
    )
