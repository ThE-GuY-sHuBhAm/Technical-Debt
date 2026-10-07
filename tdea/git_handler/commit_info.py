"""FR-07: the product's record of one commit.

For each commit: SHA-1 hash, the first 72 characters of the message as git stores it,
author name, author email, author time in ISO 8601 UTC, and the changed files with
their change type (added, modified or deleted).

Changed files come from ``git diff-tree`` against the commit's parent, with rename
detection off, so a rename is a deleted and an added entry; a merge commit has no
entries (execution plan, Task 3 decision 4). A type change at the same path (file to
symbolic link) is recorded as modified. This is the product history only; the research
history (first-parent, cut-off) lives in separate classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from git.objects import Commit

MESSAGE_LENGTH = 72
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ChangeType(Enum):
    """The three change types FR-07 records."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


_STATUS = {
    "A": ChangeType.ADDED,
    "M": ChangeType.MODIFIED,
    "T": ChangeType.MODIFIED,
    "D": ChangeType.DELETED,
}


@dataclass(frozen=True)
class ChangedFile:
    """One path a commit changed, and how."""

    path: str
    change_type: ChangeType


@dataclass(frozen=True)
class CommitInfo:
    """The FR-07 fields of one commit."""

    sha: str
    message: str
    author_name: str
    author_email: str
    authored_at: str
    changed_files: tuple[ChangedFile, ...]

    @classmethod
    def from_commit(cls, commit: Commit) -> CommitInfo:
        """Build the record for a GitPython commit."""
        message = commit.message
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")
        return cls(
            sha=commit.hexsha,
            message=message[:MESSAGE_LENGTH],
            author_name=commit.author.name or "",
            author_email=commit.author.email or "",
            authored_at=to_utc(commit.authored_datetime),
            changed_files=changed_files(commit),
        )


def to_utc(moment: datetime) -> str:
    """Return a timezone-aware time as ISO 8601 UTC, for example 2026-09-10T13:00:00Z."""
    return moment.astimezone(timezone.utc).strftime(UTC_FORMAT)


def changed_files(commit: Commit) -> tuple[ChangedFile, ...]:
    """Return the files ``commit`` changed against its parent; none for a merge."""
    if len(commit.parents) > 1:
        return ()
    output = commit.repo.git.diff_tree(
        "--no-commit-id", "-r", "--root", "--no-renames", "--name-status", "-z",
        commit.hexsha,
    )
    fields = output.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    files = []
    for status, path in zip(fields[0::2], fields[1::2]):
        try:
            change_type = _STATUS[status]
        except KeyError as exc:
            raise ValueError(f"unexpected change status {status!r} in {commit.hexsha}") from exc
        files.append(ChangedFile(path, change_type))
    return tuple(files)
