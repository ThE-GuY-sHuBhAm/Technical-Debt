"""RE-20: byte-identical exports with and without the lint cache (NFR-24, NFR-25; sprint 23 Oct).

RG1 part: one run file with pinned HEADs and cut-off and one key, run into three fresh
stores: with no lint cache, again with no lint cache (lintcache.db deleted), and with the
first run's warm cache. Each run is exported; every record file has the same SHA-256 in
all three exports, and the warm run lints nothing anew (counted by the profile; TDEA
prints no cache-hit count, see below). RG10 part: FX-RG10 v2 evaluated twice, into two
fresh stores with the same key, gives byte-identical exports.

BLOCKED as an acceptance check: the RG1 runs use the SYNTHETIC — not CL-03 doubles, so
this is mechanics evidence only; FX-RG10 v2 is synthetic too. Gap noted for the sprint
report: RE-20 says the warm run "reports cache hits"; the command line prints no cache
count, so the hits are shown through CountingProfile instead.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

from make_fx_rg10 import FxRg10
from rg10_run import run_file_text as rg10_run_file
from run_setup import Built
from run_support import (FX_PAIRS_URL, FX_PAIRS_WINDOWS, FX_RG1_URL, FX_RG1_WINDOWS,
                         LocalAccess, Workspace, rows, run_file_text)
from test_re01_run import CountingProfile

from tdea.api.cli import EXIT_OK, main


def head(path: Path) -> str:
    """Return the HEAD commit of a fixture repository."""
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def export_hashes(store: Path, out: Path) -> dict[str, str]:
    """Export every run in ``store`` as CSV and return each record file's SHA-256."""
    run_ids = [run_id for run_id, in rows(store, "SELECT run_id FROM run ORDER BY run_id")]
    assert main(["research", "export", "--store", str(store), "--out", str(out),
                 *run_ids]) == EXIT_OK
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(out.iterdir()) if path.name != "metadata.json"}


def share_key(source: Workspace, target: Workspace) -> None:
    """Give ``target`` the key file of ``source`` (RE-20: same key)."""
    destination = target.paths().key_file
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source.paths().key_file, destination)
    destination.chmod(0o600)  # the KeyStore refuses a key file others can read


def test_rg1_exports_are_identical_with_the_cache_off_and_on(built: Built,
                                                              tmp_path: Path) -> None:
    """Steps 1-4: three stores, cold, cold again and warm; all record files identical."""
    text = run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS, head(built.fx_rg1.path)),
                          (FX_PAIRS_URL, FX_PAIRS_WINDOWS, head(built.fx_pairs))])
    first = built.workspace(tmp_path / "first", warm=False)
    assert main(["research", "run", str(first.run_file(text))],
                service=first.service()) == EXIT_OK
    second = built.workspace(tmp_path / "second", warm=False)
    share_key(first, second)
    assert not second.paths().lint_cache.exists()
    assert main(["research", "run", str(second.run_file(text))],
                service=second.service()) == EXIT_OK
    third = built.workspace(tmp_path / "third", warm=False)
    share_key(first, third)
    third.paths().lint_cache.parent.mkdir(parents=True)
    shutil.copyfile(first.paths().lint_cache, third.paths().lint_cache)
    profile = CountingProfile()
    assert main(["research", "run", str(third.run_file(text))],
                service=third.service(profile=profile)) == EXIT_OK
    assert profile.requests and not profile.fresh  # every lint answered by the cache
    hashes = [export_hashes(workspace.store, tmp_path / f"export-{workspace.root.name}")
              for workspace in (first, second, third)]
    assert hashes[0] == hashes[1] == hashes[2]
    assert len(hashes[0]) == 22  # 21 entities and data_dictionary.csv


def test_two_rg10_evaluations_export_identically(fx_rg10_v2: FxRg10, tmp_path: Path) -> None:
    """Step 5: FX-RG10 v2 evaluated twice with the same key; both exports identical."""
    workspaces = [Workspace(tmp_path / name, LocalAccess({})) for name in ("one", "two")]
    for number, workspace in enumerate(workspaces):
        workspace.root.mkdir()
        if number:
            share_key(workspaces[0], workspace)
        assert main(["research", "run", str(workspace.run_file(rg10_run_file(
            fx_rg10_v2.path)))], service=workspace.service()) == 2
    one, two = (export_hashes(workspace.store, tmp_path / f"export-{workspace.root.name}")
                for workspace in workspaces)
    assert one == two and len(one) == 22
