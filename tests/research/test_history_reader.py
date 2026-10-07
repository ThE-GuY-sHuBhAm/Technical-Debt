"""HistoryReader edge cases: cut-off, pinned HEAD, root and empty histories, no checkout.

Small repositories are built here with the deterministic fixture git; FX-RG1 covers
merges, time zones and author versus committer times.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fixture_git import FixtureRepo, Identity
from fx_rg1_clone import ClonedFxRg1

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.history_reader import HistoryReader
from tdea.git_handler.local_repository import LocalRepository

DEV = Identity("Test Developer", "dev@example.invalid")
UTC = timezone.utc
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


def _three_commits(path: Path) -> list[str]:
    """Build a linear history whose middle commit is dated after its child (clock skew)."""
    repo = FixtureRepo(path)
    return [
        repo.commit("one", {"a.py": "A = 1\n"}, author=DEV, date="2026-01-01T00:00:00+00:00"),
        repo.commit("two", {"a.py": "A = 2\n"}, author=DEV, date="2026-03-01T00:00:00+00:00"),
        repo.commit("three", {"a.py": "A = 3\n"}, author=DEV,
                    date="2026-02-01T00:00:00+00:00"),
    ]


@pytest.fixture(name="skewed")
def _skewed(tmp_path: Path) -> tuple[HistoryReader, list[str]]:
    """Return a reader over a cloned three-commit history with a skewed middle date."""
    shas = _three_commits(tmp_path / "source")
    local = LocalRepository(tmp_path)
    local.clone(tmp_path / "source")
    yield HistoryReader(local), shas
    local.cleanup()


def test_cutoff_is_inclusive_and_excluded_commits_are_counted(skewed) -> None:
    """A commit dated exactly at the cut-off is kept; one dated after is left out and counted."""
    reader, shas = skewed
    history = reader.first_parent(shas[2], datetime(2026, 2, 1, tzinfo=UTC))
    assert [commit.sha for commit in history.commits] == [shas[0], shas[2]]
    assert history.excluded_after_cutoff == 1


def test_positions_follow_the_chain_not_the_cutoff(skewed) -> None:
    """A commit keeps its first-parent position when an earlier one is cut off."""
    reader, shas = skewed
    history = reader.first_parent(shas[2], datetime(2026, 2, 1, tzinfo=UTC))
    assert [commit.position for commit in history.commits] == [0, 2]


def test_pinned_head_ends_the_history_there(skewed) -> None:
    """A pinned earlier HEAD (given as an abbreviated SHA) returns history up to it."""
    reader, shas = skewed
    history = reader.first_parent(shas[1][:10], FAR_FUTURE)
    assert history.head == shas[1]
    assert [commit.sha for commit in history.commits] == shas[:2]


def test_unknown_head_is_a_structured_error(skewed) -> None:
    """A HEAD that is not in the clone gives a three-part HISTORY_FAILED error."""
    reader, _shas = skewed
    with pytest.raises(TdeaError) as caught:
        reader.first_parent("0" * 40, FAR_FUTURE)
    assert caught.value.code is ErrorCode.HISTORY_FAILED


def test_naive_cutoff_is_refused(skewed) -> None:
    """A cut-off without a time zone is refused rather than guessed."""
    reader, shas = skewed
    with pytest.raises(ValueError):
        reader.first_parent(shas[2], datetime(2026, 2, 1))


def test_root_only_history(tmp_path: Path) -> None:
    """A single root commit has position 0, no parents and no merge flag."""
    repo = FixtureRepo(tmp_path / "source")
    sha = repo.commit("root", {"a.py": "A = 1\n"}, author=DEV, date="2026-01-01T00:00:00+05:30")
    with LocalRepository(tmp_path) as local:
        local.clone(tmp_path / "source")
        (commit,) = HistoryReader(local).first_parent(sha, FAR_FUTURE).commits
    assert (commit.sha, commit.position, commit.parents, commit.is_merge) == (sha, 0, (), False)
    assert commit.authored_at == datetime(2025, 12, 31, 18, 30, tzinfo=UTC)


def test_empty_repository_has_no_history(tmp_path: Path) -> None:
    """A clone of a repository without commits has no HEAD to record."""
    FixtureRepo(tmp_path / "empty")
    with LocalRepository(tmp_path) as local:
        local.clone(tmp_path / "empty")
        with pytest.raises(TdeaError) as caught:
            HistoryReader(local).clone_metadata()
    assert caught.value.code is ErrorCode.HISTORY_FAILED


def test_reader_needs_a_clone(tmp_path: Path) -> None:
    """Reading before clone() is a programming error."""
    with pytest.raises(RuntimeError):
        HistoryReader(LocalRepository(tmp_path)).clone_metadata()


def test_side_branch_commits_are_not_in_first_parent_history(
    cloned_fx_rg1: ClonedFxRg1,
) -> None:
    """Commits reached only through a merge's second parent are not part of the history."""
    shas = {commit.sha for commit in cloned_fx_rg1.history.commits}
    assert cloned_fx_rg1.fixture.shas["feature_1"] not in shas
    assert cloned_fx_rg1.fixture.shas["feature_2"] not in shas


def test_author_and_committer_times_are_kept_apart(cloned_fx_rg1: ClonedFxRg1) -> None:
    """The AI-committer commit was authored at 09:00 and committed at 09:05 UTC."""
    commit = cloned_fx_rg1.commit("ai_committer")
    assert commit.authored_at == datetime(2026, 2, 16, 9, 0, tzinfo=UTC)
    assert commit.committed_at == datetime(2026, 2, 16, 9, 5, tzinfo=UTC)


def test_raw_strings_are_available_for_provenance(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Raw author, committer and full message (with trailers) stay in memory for FR-54."""
    raw = cloned_fx_rg1.commit("ai_committer").raw
    assert (raw.author_name, raw.committer_name) == ("Ravi Menon", "copilot-swe-agent[bot]")
    message = cloned_fx_rg1.commit("trailers").raw.message
    assert "Co-authored-by: Claude <noreply@anthropic.com>" in message


def test_reading_history_checks_nothing_out(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Reading the history leaves HEAD and the working tree as the clone left them."""
    path = cloned_fx_rg1.local.path
    clone_git = cloned_fx_rg1.local.repo.git  # the clone's own settings, as it was checked out
    head_before = clone_git.rev_parse("HEAD")
    reader = HistoryReader(cloned_fx_rg1.local)
    reader.first_parent(cloned_fx_rg1.fixture.shas["util_edit"], FAR_FUTURE)
    assert clone_git.rev_parse("HEAD") == head_before
    assert clone_git.status("--porcelain") == ""
    assert (path / "pkg" / "helpers.py").is_file()
    assert not (path / "pkg" / "util.py").exists()