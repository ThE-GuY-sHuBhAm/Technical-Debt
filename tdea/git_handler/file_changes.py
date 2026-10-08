"""File-change records of one commit against its first parent (FR-50).

A record holds the change type (A, M, D or R), the old and new path and blob, the rename
similarity, whether git treats the file as binary, and the changed-line hunks as (old
start, old length, new start, new length). Line counts follow from the hunks, which git
computes with no context lines, so they equal git's own added and removed counts. A
rename, with or without edits, is one R record that keeps its old path (FR-50, RE-03).
Binary files get no hunks.

The records are parsed from two git diff-tree runs with the same options: ``--raw -z``
for the file pairs and ``-p --unified=0`` for the hunks. Git writes both in the same
order, one patch section per pair, so they are matched by position; with ``--full-index``
each section's blob IDs are checked against its pair. The one exception
is a type change (T, for example a file replaced by a symbolic link), which git's patch
writes as a deletion and an addition; it becomes a D and an A record, as git diff shows
it. Nothing here runs git; HistoryReader does.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX = re.compile(r"^index ([0-9a-f]{40,64})\.\.([0-9a-f]{40,64})(?: |$)")
_SECTION = "diff --git "
_BINARY = "Binary files "


class ChangeType(Enum):
    """The four FR-50 change types."""

    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"


@dataclass(frozen=True)
class Hunk:
    """One changed-line range pair; a length of 0 means no lines on that side."""

    old_start: int
    old_length: int
    new_start: int
    new_length: int


@dataclass(frozen=True)
class FileSide:
    """A path and its blob on one side of a change."""

    path: str
    blob: str


@dataclass(frozen=True)
class FileChange:
    """One changed file of a commit against its first parent."""

    change_type: ChangeType
    old: FileSide | None
    new: FileSide | None
    similarity: int | None
    binary: bool
    hunks: tuple[Hunk, ...]

    @property
    def lines_added(self) -> int | None:
        """Return the number of added lines, or None for a binary file."""
        return None if self.binary else sum(hunk.new_length for hunk in self.hunks)

    @property
    def lines_removed(self) -> int | None:
        """Return the number of removed lines, or None for a binary file."""
        return None if self.binary else sum(hunk.old_length for hunk in self.hunks)


@dataclass(frozen=True)
class _RawPair:
    """One file pair of git's raw output."""

    status: str
    similarity: int | None
    old: FileSide | None
    new: FileSide | None


@dataclass(frozen=True)
class _Section:
    """One patch section: whether it is binary, its hunks and its blob IDs, if shown."""

    binary: bool
    hunks: tuple[Hunk, ...]
    blobs: tuple[str, str] | None


def parse_changes(raw: bytes, patch: bytes) -> tuple[FileChange, ...]:
    """Combine ``diff-tree --raw -z`` and ``diff-tree -p --unified=0`` output into records."""
    pairs = list(_raw_pairs(raw))
    sections = _sections(patch.decode("utf-8", errors="replace"))
    changes: list[FileChange] = []
    for pair in pairs:
        if pair.status == "T":
            taken = _take(sections, 2)
            removed = _checked(taken[0], pair.old, None)
            added = _checked(taken[1], None, pair.new)
            changes.append(FileChange(ChangeType.DELETED, pair.old, None, None,
                                      removed.binary, removed.hunks))
            changes.append(FileChange(ChangeType.ADDED, None, pair.new, None,
                                      added.binary, added.hunks))
            continue
        section = _checked(_take(sections, 1)[0], pair.old, pair.new)
        changes.append(FileChange(ChangeType(pair.status), pair.old, pair.new,
                                  pair.similarity, section.binary, section.hunks))
    if sections:
        raise ValueError("git wrote more patch sections than file pairs")
    return tuple(changes)


def _checked(section: _Section, old: FileSide | None, new: FileSide | None) -> _Section:
    """Return ``section`` after checking that its blob IDs, if shown, are the pair's."""
    if section.blobs is None:
        return section
    expected = (old.blob if old else None, new.blob if new else None)
    shown = tuple(None if set(blob) == {"0"} else blob for blob in section.blobs)
    if shown != expected:
        raise ValueError("git's patch sections do not line up with its file pairs")
    return section


def _take(sections: list[_Section], count: int) -> list[_Section]:
    """Remove and return the next ``count`` sections, or fail if git wrote fewer."""
    if len(sections) < count:
        raise ValueError("git wrote fewer patch sections than file pairs")
    taken = sections[:count]
    del sections[:count]
    return taken


def _raw_pairs(raw: bytes) -> Iterator[_RawPair]:
    """Yield the file pairs of ``git diff-tree --raw -z`` output."""
    tokens = raw.split(b"\0")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        _, _, old_blob, new_blob, status = token.decode("ascii")[1:].split(" ")
        letter = status[0]
        if letter not in "AMDRT":
            raise ValueError(f"unexpected git change status {status!r}")
        paths = [path.decode("utf-8", errors="surrogateescape")
                 for path in tokens[index:index + (2 if letter == "R" else 1)]]
        index += len(paths)
        yield _RawPair(
            status=letter,
            similarity=int(status[1:]) if letter == "R" else None,
            old=None if set(old_blob) == {"0"} else FileSide(paths[0], old_blob),
            new=None if set(new_blob) == {"0"} else FileSide(paths[-1], new_blob),
        )


def _sections(patch: str) -> list[_Section]:
    """Split patch output into one section per file pair."""
    sections: list[_Section] = []
    current: dict | None = None
    for line in patch.split("\n"):
        if line.startswith(_SECTION):
            if current is not None:
                sections.append(_Section(**current))
            current = {"binary": False, "hunks": (), "blobs": None}
        elif current is None:
            continue
        elif line.startswith(_BINARY):
            current["binary"] = True
        elif (match := _HUNK.match(line)) is not None:
            current["hunks"] += (_hunk(match),)
        elif not current["hunks"] and (match := _INDEX.match(line)) is not None:
            current["blobs"] = (match.group(1), match.group(2))
    if current is not None:
        sections.append(_Section(**current))
    return sections


def _hunk(match: re.Match[str]) -> Hunk:
    """Build a Hunk from a header match; an omitted length means 1, as in git."""
    old_start, old_length, new_start, new_length = match.groups()
    return Hunk(int(old_start), 1 if old_length is None else int(old_length),
                int(new_start), 1 if new_length is None else int(new_length))
