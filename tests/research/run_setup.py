"""Shared setup for the run tests: built fixtures, a warm lint cache and workspaces.

SYNTHETIC — not CL-03 (run_support): every run here uses test doubles for provenance
and the package filter, so these runs are mechanics evidence only.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import make_fx_pairs
import make_fx_rg1_v2
from run_support import LocalAccess, Workspace, four_units

from tdea.api.cli import main


@dataclass(frozen=True)
class Built:
    """FX-RG1 v2 and FX-PAIRS, built once, and a lint cache warmed by one full run."""

    fx_rg1: make_fx_rg1_v2.FxRg1V2
    fx_pairs: Path
    warm_cache: Path

    def sources(self) -> dict[str, Path]:
        """Return the local fixture for each placeholder repository."""
        return {"tdea-fixtures/fx-rg1": self.fx_rg1.path,
                "tdea-fixtures/fx-pairs": self.fx_pairs}

    def workspace(self, root: Path, warm: bool = True) -> Workspace:
        """Return a new workspace, with a copy of the warm lint cache unless ``warm`` is off."""
        root.mkdir(parents=True, exist_ok=True)
        workspace = Workspace(root, LocalAccess(self.sources()))
        if warm:
            target = workspace.paths().lint_cache
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.warm_cache, target)
        return workspace


def build(root: Path, fx_rg1: make_fx_rg1_v2.FxRg1V2) -> Built:
    """Build FX-PAIRS and warm a lint cache with one full four-unit run."""
    fx_pairs = root / "fx-pairs"
    make_fx_pairs.build(fx_pairs)
    built = Built(fx_rg1, fx_pairs, root / "unused")
    warmer = built.workspace(root / "warmer", warm=False)
    assert main(["research", "run", str(warmer.run_file(four_units()))],
                service=warmer.service()) == 0
    return Built(fx_rg1, fx_pairs, warmer.paths().lint_cache)
