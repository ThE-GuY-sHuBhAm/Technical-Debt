"""Child process for the NFR-25 kill test: run, and wait inside a unit's transaction.

Usage: python kill_helper.py WORKSPACE FX_RG1 FX_PAIRS RUN_FILE MARKER UNIT

Runs the run file with the SYNTHETIC — not CL-03 doubles of run_support. When unit UNIT
has written all its records but not yet committed them, it writes MARKER and waits, so
the test can kill the process at that point (SIGKILL on POSIX, TerminateProcess on
Windows). The test file adds the fixture folder to the import path.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from run_support import LocalAccess, Workspace

from tdea.aggregator.research_job import PlannedUnit, RunObserver


class PauseInUnit(RunObserver):
    """Waits, without committing, once the chosen unit's records are written."""

    def __init__(self, number: int, marker: Path) -> None:
        """Pause in unit ``number``; announce it by creating ``marker``."""
        self.number = number
        self.marker = marker

    def unit_written(self, unit: PlannedUnit, total: int) -> None:
        """Announce the pause and wait to be killed."""
        if unit.number == self.number:
            self.marker.write_text("written", encoding="utf-8")
            time.sleep(600)


def main(argv: list[str]) -> int:
    """Run until killed inside the chosen unit."""
    root, fx_rg1, fx_pairs, run_file, marker, unit = argv
    workspace = Workspace(Path(root), LocalAccess({"tdea-fixtures/fx-rg1": Path(fx_rg1),
                                                   "tdea-fixtures/fx-pairs": Path(fx_pairs)}))
    workspace.service().run(run_file, observer=PauseInUnit(int(unit), Path(marker)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
