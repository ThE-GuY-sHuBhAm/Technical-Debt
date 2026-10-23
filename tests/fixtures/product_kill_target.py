"""Child process for TC-130: runs one FX-100 analysis and reports its progress.

Usage: product_kill_target.py <workspace> <fx-100 fixture path>. Each line printed is
the percentage reached so far. The test kills this process (SIGKILL on POSIX) while the
analysis is running, then checks the database it leaves behind.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from product_support import local_service


def main(workspace: str, fixture: str) -> None:
    """Start the analysis and print its percentage until it ends."""
    service = local_service(Path(workspace), {"tdea-fixtures/fx-100": Path(fixture)})
    submission = service.submit("https://github.com/tdea-fixtures/fx-100")
    last = -1
    while True:
        progress = service.progress(submission.repository_id)
        if progress is None:
            return
        if progress.percent != last:
            last = progress.percent
            print(last, flush=True)
        time.sleep(0.02)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
