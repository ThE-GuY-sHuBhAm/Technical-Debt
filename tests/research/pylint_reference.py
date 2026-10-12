"""The plain Pylint CLI run that the research profile is compared with (RE-04 step 4).

The disable list is typed out from FR-51 (a) here rather than taken from the profile, so
the comparison does not trust the code it checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# FR-51 (a), expanded by hand: C0103, C0114-C0116, C0301, C0303-C0305, C0411-C0415, E0401,
# E0611 and R0801.
FROZEN_DISABLE = ["C0103", "C0114", "C0115", "C0116", "C0301", "C0303", "C0304", "C0305",
                  "C0411", "C0412", "C0413", "C0414", "C0415", "E0401", "E0611", "R0801"]


def cli_messages(name: str, content: bytes, folder: Path) -> list[dict]:
    """Run the Pylint CLI exactly as RE-04 step 4 states it, on one file in ``folder``.

    Pylint's stats file goes to a PYLINTHOME beside ``folder`` so the test never writes
    to the developer's home; that setting does not change the messages.
    """
    folder.mkdir()
    (folder / name).write_bytes(content)
    rcfile = folder.parent / f"{folder.name}.empty-rc"
    rcfile.write_text("", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "pylint", f"--rcfile={rcfile}",
         f"--disable={','.join(FROZEN_DISABLE)}", "--output-format=json", name],
        cwd=folder, capture_output=True, check=False,
        env=dict(os.environ, PYLINTHOME=str(folder.parent / f"{folder.name}.pylinthome")))
    return json.loads(completed.stdout.decode("utf-8"))
