"""A clone of FX-RG1 and its first-parent history, shared by the research tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1

from tdea.git_handler.history_reader import CloneMetadata, FirstParentHistory, HistoryReader
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.research_commit import ResearchCommit

CLONE_TIME = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ClonedFxRg1:
    """FX-RG1, a clone of it and what the history reader returns for the clone."""

    fixture: make_fx_rg1.FxRg1
    local: LocalRepository
    metadata: CloneMetadata
    history: FirstParentHistory

    def commit(self, label: str) -> ResearchCommit:
        """Return the history's commit for a fixture label."""
        sha = self.fixture.shas[label]
        return next(commit for commit in self.history.commits if commit.sha == sha)


@contextmanager
def clone_fx_rg1(fixture: make_fx_rg1.FxRg1, temp_root: Path) -> Iterator[ClonedFxRg1]:
    """Clone FX-RG1 with a fixed clock and read its history up to the clone time."""
    with LocalRepository(temp_root, clock=lambda: CLONE_TIME) as local:
        local.clone(fixture.path)
        reader = HistoryReader(local)
        metadata = reader.clone_metadata()
        history = reader.first_parent(metadata.head_sha, metadata.cloned_at)
        yield ClonedFxRg1(fixture, local, metadata, history)