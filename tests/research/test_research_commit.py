"""ResearchCommit: an immutable, memory-only record with UTC times and hidden raw strings."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit

UTC = timezone.utc
MOMENT = datetime(2026, 1, 1, tzinfo=UTC)
RAW = RawCommitStrings("Asha Rao", "asha@example.invalid", "Bot", "bot@example.invalid",
                       "Fix\n\nCo-authored-by: Asha Rao <asha@example.invalid>")


def make(**changes: object) -> ResearchCommit:
    """Return a valid commit with some fields changed."""
    fields = {"sha": "a" * 40, "position": 0, "parents": (), "authored_at": MOMENT,
              "committed_at": MOMENT, "raw": RAW}
    return ResearchCommit(**{**fields, **changes})


def test_merge_flag_follows_the_parents() -> None:
    """The merge flag is true exactly when a commit has more than one parent."""
    assert not make(parents=("b" * 40,)).is_merge
    assert make(parents=("b" * 40, "c" * 40)).is_merge


def test_commit_is_immutable() -> None:
    """Records cannot be changed after the history reader builds them."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        make().position = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    "moment",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5, minutes=30)))],
)
def test_timestamps_must_be_utc(moment: datetime) -> None:
    """Naive times and non-UTC offsets are refused."""
    with pytest.raises(ValueError):
        make(authored_at=moment)
    with pytest.raises(ValueError):
        make(committed_at=moment)


@pytest.mark.parametrize("changes", [{"sha": "abc"}, {"sha": "A" * 40}, {"position": -1}])
def test_sha_and_position_are_checked(changes: dict[str, object]) -> None:
    """Only full lower-case SHAs and non-negative positions are accepted."""
    with pytest.raises(ValueError):
        make(**changes)


def test_repr_withholds_raw_identities() -> None:
    """NFR-27: printing or logging a commit never shows raw names, e-mails or messages."""
    text = repr(make())
    for raw in ("Asha", "asha@example.invalid", "bot@example.invalid", "Co-authored-by"):
        assert raw not in text
    assert "a" * 40 in text
