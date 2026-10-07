"""LocalRepository's additive clone record (Task 4): source, clone time and repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from repo_builder import RepoBuilder

from tdea.git_handler.local_repository import LocalRepository

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(name="source")
def _source(tmp_path: Path) -> Path:
    """Return a one-commit source repository."""
    builder = RepoBuilder(tmp_path / "source")
    builder.commit("One", {"a.py": "A = 1\n"})
    return builder.path


def test_clone_records_source_and_clone_time_in_utc(source: Path, tmp_path: Path) -> None:
    """The address and the clock's time (converted to UTC) are kept until cleanup."""
    local = LocalRepository(tmp_path, clock=lambda: datetime(2026, 10, 7, 17, 30, tzinfo=IST))
    local.clone(source)
    assert local.source == str(source)
    assert local.cloned_at == datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    assert local.repo.head.commit.message.startswith("One")
    local.cleanup()
    assert (local.source, local.cloned_at) == (None, None)


def test_default_clock_is_the_current_utc_time(source: Path, tmp_path: Path) -> None:
    """Without a clock, the clone time is taken in UTC when the clone completes."""
    before = datetime.now(timezone.utc)
    with LocalRepository(tmp_path) as local:
        local.clone(source)
        after = datetime.now(timezone.utc)
        assert before <= local.cloned_at <= after
        assert local.cloned_at.utcoffset() == timedelta(0)


def test_naive_clock_is_refused_and_the_clone_removed(source: Path, tmp_path: Path) -> None:
    """A clock without a time zone is an error, and the clone does not stay behind."""
    clones = tmp_path / "clones"
    clones.mkdir()
    local = LocalRepository(clones, clock=lambda: datetime(2026, 10, 7, 12, 0))
    with pytest.raises(ValueError):
        local.clone(source)
    assert local.path is None and not list(clones.iterdir())


def test_repository_needs_a_clone(tmp_path: Path) -> None:
    """The GitPython repository is only available while a clone exists."""
    with pytest.raises(RuntimeError):
        _ = LocalRepository(tmp_path).repo
