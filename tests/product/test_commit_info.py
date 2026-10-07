"""FR-07 commit records: the field shapes of TC-020 to TC-024 on test-built repositories.

The FX-S comparisons against git log follow on Day 2 with make_fx_product.py. Renames
and merges follow decision 4 of the execution plan.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import git
import pytest
from repo_builder import RepoBuilder

from tdea.git_handler.commit_info import ChangedFile, ChangeType, CommitInfo, to_utc


@pytest.fixture(name="builder")
def _builder(tmp_path: Path) -> RepoBuilder:
    """Return an empty repository to build commits in."""
    return RepoBuilder(tmp_path / "repo")


def info(builder: RepoBuilder, sha: str) -> CommitInfo:
    """Return the CommitInfo of one commit."""
    with git.Repo(builder.path) as repo:
        return CommitInfo.from_commit(repo.commit(sha))


def test_hash_and_author_match_git_log(builder: RepoBuilder) -> None:
    """SHA-1, author name and author email equal git log's (TC-020, TC-022 shape)."""
    sha = builder.commit("Add module", {"a.py": "A = 1\n"},
                         author=("Meera Iyer", "meera@example.invalid"))
    record = info(builder, sha)
    assert record.sha == builder.log("%H")[0] == sha
    assert f"{record.author_name}|{record.author_email}" == builder.log("%an|%ae")[0]


def test_message_keeps_its_first_72_characters(builder: RepoBuilder) -> None:
    """A 120-character message is stored as its first 72 characters (TC-021)."""
    message = "".join(chr(ord("a") + index % 26) for index in range(120))
    record = info(builder, builder.commit(message, {"a.py": "A = 1\n"}))
    assert record.message == message[:72]
    assert len(record.message) == 72


def test_author_time_is_iso_8601_utc(builder: RepoBuilder) -> None:
    """18:30 at +05:30 on 10 Sep 2026 is stored as 2026-09-10T13:00:00Z (TC-023)."""
    sha = builder.commit("Timed", {"a.py": "A = 1\n"}, date="2026-09-10T18:30:00+05:30")
    assert info(builder, sha).authored_at == "2026-09-10T13:00:00Z"


def test_added_modified_and_deleted_files(builder: RepoBuilder) -> None:
    """One commit that adds a.py, modifies b.py and deletes c.py gives three entries (TC-024)."""
    builder.commit("Start", {"b.py": "B = 1\n", "c.py": "C = 1\n"})
    sha = builder.commit("Change", {"a.py": "A = 1\n", "b.py": "B = 2\n", "c.py": None})
    assert info(builder, sha).changed_files == (
        ChangedFile("a.py", ChangeType.ADDED),
        ChangedFile("b.py", ChangeType.MODIFIED),
        ChangedFile("c.py", ChangeType.DELETED),
    )


def test_root_commit_lists_every_file_as_added(builder: RepoBuilder) -> None:
    """The first commit has no parent, so all its files are added."""
    sha = builder.commit("Start", {"a.py": "A = 1\n", "pkg/b.py": "B = 1\n"})
    assert info(builder, sha).changed_files == (
        ChangedFile("a.py", ChangeType.ADDED),
        ChangedFile("pkg/b.py", ChangeType.ADDED),
    )


def test_rename_is_a_deletion_and_an_addition(builder: RepoBuilder) -> None:
    """Rename detection is off: old path deleted, new path added (decision 4)."""
    builder.commit("Start", {"old_name.py": "VALUE = 1\n" * 20})
    builder.git("mv", "old_name.py", "new_name.py")
    sha = builder.commit("Rename", {})
    assert info(builder, sha).changed_files == (
        ChangedFile("new_name.py", ChangeType.ADDED),
        ChangedFile("old_name.py", ChangeType.DELETED),
    )


def test_merge_commit_has_no_changed_files(builder: RepoBuilder) -> None:
    """A merge commit produces no changed-file entries (decision 4)."""
    builder.commit("Start", {"a.py": "A = 1\n"})
    builder.git("checkout", "-q", "-b", "feature")
    builder.commit("Feature", {"f.py": "F = 1\n"})
    builder.git("checkout", "-q", "main")
    builder.commit("Main work", {"m.py": "M = 1\n"})
    builder.git("merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    record = info(builder, builder.head())
    assert record.message.startswith("Merge feature")
    assert not record.changed_files


def test_to_utc_keeps_the_instant() -> None:
    """A negative offset converts forward to UTC and ends in Z."""
    moment = datetime(2026, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert to_utc(moment) == "2026-01-01T05:30:00Z"