"""One RG10 evaluation of FX-RG10 v2 through ``tdea research run`` (sprint 23 Oct).

evaluate() runs the bundle once in a fresh workspace, as RE-17 step 1 asks, with every
hostile output's target set: the sentinel, spawn and init-hook paths point into the
workspace and a local listener waits on an ephemeral port. Afterwards it records which of
those files exist (or exist under their default names in the working folder), whether
the listener was ever connected to, how long the evaluation took, and the store before
and after the same run file is started again. The workspace
uses the pinned environment facts (run_support), so the run needs no network and no
CL-03 list: RG10 runs use none.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from pathlib import Path

import make_fx_rg10_v2
import pytest
from make_fx_rg10 import SENTINEL_VARIABLE
from run_support import LocalAccess, Workspace, dump

from tdea.api.cli import main

DEFAULT_NAMES = ("fx_rg10_sentinel.txt", "fx_rg10_spawn.txt", "fx_rg10_hook.txt")


@dataclass(frozen=True)
class Rg10Evaluation:
    """What one evaluation and one restart of FX-RG10 v2 did."""

    workspace: Workspace
    run_file: Path
    exit_code: int
    restart_exit_code: int
    seconds: float
    written: tuple[str, ...]  # the hostile targets that exist afterwards
    listener_connected: bool
    before_restart: dict[str, list[tuple]]
    after_restart: dict[str, list[tuple]]

    @property
    def store(self) -> Path:
        """Return research.db."""
        return self.workspace.store


def run_file_text(bundle: Path) -> str:
    """Return an RG10 run file for ``bundle`` with the default smell mapping."""
    return f"kind: rg10\nstore: research.db\nbundle: '{bundle.as_posix()}'\n"


def evaluate(bundle: Path, root: Path) -> Rg10Evaluation:
    """Evaluate ``bundle`` in a new workspace at ``root`` and start it again."""
    workspace = Workspace(root, LocalAccess({}))
    root.mkdir(parents=True, exist_ok=True)
    targets = {SENTINEL_VARIABLE: root / "sentinel.txt",
               make_fx_rg10_v2.SPAWN_VARIABLE: root / "spawn.txt",
               make_fx_rg10_v2.HOOK_VARIABLE: root / "hook.txt"}
    run_file = workspace.run_file(run_file_text(bundle))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener, \
            pytest.MonkeyPatch.context() as patch:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.setblocking(False)
        patch.setenv(make_fx_rg10_v2.PORT_VARIABLE, str(listener.getsockname()[1]))
        for variable, target in targets.items():
            patch.setenv(variable, str(target))
        patch.chdir(root)  # where the outputs' default file names would land
        started = time.monotonic()
        exit_code = main(["research", "run", str(run_file)], service=workspace.service())
        seconds = time.monotonic() - started
        before = dump(workspace.store)
        restart = main(["research", "run", str(run_file)], service=workspace.service())
        try:
            listener.accept()[0].close()
            connected = True
        except BlockingIOError:
            connected = False
    written = tuple(sorted(name for name, target in targets.items() if target.exists()))
    written += tuple(name for name in DEFAULT_NAMES if (root / name).exists())
    return Rg10Evaluation(workspace, run_file, exit_code, restart, seconds, written, connected,
                          before, dump(workspace.store))
