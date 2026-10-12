"""Hand-made commit records for FR-56 checks that a git fixture cannot reach (Day 7).

Record carries what CommitRecord holds and the classifier and the base rate read. It
lets tests give a commit an unknown trace status or two changes of one path, which
neither the real classifier nor Git produces. Tests that use it say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from tdea.analyzer.research.rg1.provenance import Provenance, TraceStatus
from tdea.git_handler.file_changes import FileChange

UTC = timezone.utc


@dataclass(frozen=True)
class Entry:
    """A hand-made ChangeRecord."""

    change: FileChange


@dataclass(frozen=True)
class Record:
    """A hand-made CommitRecord."""

    sha: str
    committed_at: datetime = datetime(2026, 1, 5, 9, tzinfo=UTC)
    provenance: Provenance = Provenance(TraceStatus.NON_TRACED, None, (), None, ())
    changes: tuple[Entry, ...] = ()
    author_name_key: str | None = None
    author_email_key: str | None = None
    message: str = "Change"


def provenance(status: TraceStatus | None, tool: str | None = None) -> Provenance:
    """Return a hand-made provenance; None stands for an unknown status."""
    return Provenance(status, None, (), tool, ())  # type: ignore[arg-type]
