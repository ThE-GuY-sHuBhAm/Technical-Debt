"""CommitSelector: committer-date windows [start, end) and SHA lists (FR-49, FR-48)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tdea.git_handler.commit_selector import CommitSelector, Window
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit

UTC = timezone.utc
RAW = RawCommitStrings("A", "a@example.invalid", "A", "a@example.invalid", "message")


def commit(number: int, day: int, parents: int = 1) -> ResearchCommit:
    """Return a synthetic commit committed at midnight UTC on 2026-01-<day>."""
    moment = datetime(2026, 1, day, tzinfo=UTC)
    return ResearchCommit(f"{number:040x}", number, tuple(f"{p:040x}" for p in range(parents)),
                          moment, moment, RAW)


HISTORY = (commit(1, 1), commit(2, 5), commit(3, 10, parents=2), commit(4, 15), commit(5, 20))


def window(start_day: int, end_day: int, commits: tuple[str, ...] | None = None) -> Window:
    """Return a January 2026 window from start_day (inclusive) to end_day (exclusive)."""
    return Window(datetime(2026, 1, start_day, tzinfo=UTC),
                  datetime(2026, 1, end_day, tzinfo=UTC), commits)


def numbers(selected: tuple[ResearchCommit, ...]) -> list[int]:
    """Return the positions of selected commits."""
    return [item.position for item in selected]


def test_start_is_inclusive_and_end_exclusive() -> None:
    """Commits at the start instant are in; commits at the end instant are out."""
    assert numbers(CommitSelector(HISTORY).select(window(5, 15))) == [2, 3]


def test_merge_commits_are_selected_like_any_other() -> None:
    """D-10: merge commits are processed, so a window includes them."""
    selected = CommitSelector(HISTORY).select(window(10, 11))
    assert numbers(selected) == [3] and selected[0].is_merge


def test_bounds_in_other_time_zones_compare_as_instants() -> None:
    """A +05:30 bound equal to midnight UTC behaves exactly like the UTC bound."""
    ist = timezone(timedelta(hours=5, minutes=30))
    bound = Window(datetime(2026, 1, 5, 5, 30, tzinfo=ist), datetime(2026, 1, 15, tzinfo=UTC))
    assert numbers(CommitSelector(HISTORY).select(bound)) == [2, 3]


def test_sha_list_selects_only_those_in_history_order() -> None:
    """Listed SHAs are selected in root-to-HEAD order; a repeated SHA counts once."""
    listed = (HISTORY[3].sha, HISTORY[1].sha, HISTORY[3].sha)
    assert numbers(CommitSelector(HISTORY).select(window(1, 21, listed))) == [2, 4]


def test_sha_outside_its_window_is_rejected() -> None:
    """FR-48: a listed SHA outside its window is an invalid selection naming the SHA."""
    with pytest.raises(TdeaError) as caught:
        CommitSelector(HISTORY).select(window(1, 6, (HISTORY[3].sha,)))
    assert caught.value.code is ErrorCode.INVALID_SELECTION
    assert HISTORY[3].sha in caught.value.cause and "outside the window" in caught.value.cause


def test_sha_absent_from_history_is_rejected() -> None:
    """FR-48: a listed SHA that is not in the history (or is after the cut-off) is rejected."""
    with pytest.raises(TdeaError) as caught:
        CommitSelector(HISTORY[:2]).select(window(1, 21, ("f" * 40, HISTORY[3].sha)))
    assert caught.value.code is ErrorCode.INVALID_SELECTION
    assert "f" * 40 in caught.value.cause and HISTORY[3].sha in caught.value.cause


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC)),
        (datetime(2026, 1, 6, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC)),
        (datetime(2026, 1, 5), datetime(2026, 1, 6, tzinfo=UTC)),
    ],
)
def test_invalid_windows_are_refused(start: datetime, end: datetime) -> None:
    """A window needs timezone-aware bounds with start before end."""
    with pytest.raises(ValueError):
        Window(start, end)