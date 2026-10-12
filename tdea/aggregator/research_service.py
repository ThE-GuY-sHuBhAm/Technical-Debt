"""ResearchService: the research run lifecycle facade the command line calls (FR-48).

The command line talks to the research engine only through this service (Freeze
section 2). Day 5 provides validate(): it reads and checks a run file and returns it,
with no side effects. It opens no connection, clones nothing and creates no file: not
the research store, the lint cache, the key file or a lock (FR-48). The repository URLs
are checked against FR-02 by the analyzer's ResearchEngine, so this layer never imports
git_handler (NFR-14).

run(), status() and the run identifier arrive with the run command (Day 6). When they
do, a run refuses to start while the CL-03 rule lists are incomplete (Task 7
decision 2).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from tdea.aggregator.run_file import RunFile, read_run_file
from tdea.analyzer.research.engine import ResearchEngine


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


class ResearchService:
    """Validates run files; later starts, resumes and lists research runs."""

    def __init__(self, engine: ResearchEngine | None = None,
                 clock: Callable[[], datetime] = _utc_now) -> None:
        """Use ``engine`` for the FR-02 check and ``clock`` for the cut-off rule."""
        self._engine = engine if engine is not None else ResearchEngine()
        self._clock = clock

    def validate(self, path: Path | str) -> RunFile:
        """Return the validated run file at ``path``, or raise RunFileInvalid (FR-48)."""
        return read_run_file(path, self._engine, self._clock())
    