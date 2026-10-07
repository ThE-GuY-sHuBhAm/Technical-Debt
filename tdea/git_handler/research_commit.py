"""ResearchCommit: one first-parent commit as the research history reads it (FR-49).

Memory only. It holds what FR-49 records about a commit before provenance runs: SHA,
first-parent position (root = 0), parent SHAs and merge flag, and author and committer
timestamps in UTC. The raw author, committer and message strings are grouped in
RawCommitStrings, the part FR-54 classifies and then replaces with identity keys and a
rewritten message. Raw identities must never be written anywhere (NFR-27), so their
repr is withheld. The changed paths with change type and rename source arrive with
FR-50 (Day 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, repr=False)
class RawCommitStrings:
    """The raw identity and message strings of a commit, kept in memory only (NFR-27)."""

    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    message: str

    def __repr__(self) -> str:
        """Withhold the raw strings so they cannot reach a log or file by accident."""
        return "RawCommitStrings(<withheld>)"


@dataclass(frozen=True)
class ResearchCommit:
    """One commit of a first-parent history."""

    sha: str
    position: int
    parents: tuple[str, ...]
    authored_at: datetime
    committed_at: datetime
    raw: RawCommitStrings

    def __post_init__(self) -> None:
        """Check the SHA, the position and that both timestamps are in UTC."""
        if len(self.sha) != 40 or any(char not in "0123456789abcdef" for char in self.sha):
            raise ValueError(f"not a full commit SHA: {self.sha!r}")
        if self.position < 0:
            raise ValueError("a first-parent position cannot be negative")
        for moment in (self.authored_at, self.committed_at):
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError("research timestamps must be timezone-aware UTC")

    @property
    def is_merge(self) -> bool:
        """Return True for a commit with more than one parent (the FR-49 merge flag)."""
        return len(self.parents) > 1