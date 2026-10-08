"""A stand-in for the research harness's preparation step (RG10 pipeline step 3).

It does what the harness does with TDEA's public functions, imported from the installed
package: read the variant module and the output file from the bundle as UTF-8, build the
modified module with prepare_module, and hash the executed tree with tree_sha256 before
execution. materialise() also lays the executed tree out on disk the way the harness's
sandbox copy would be (variant copy, modified module written with LF endings, hidden tests
under .hidden_tests/<task_id>/), so a test can hash the real files independently.
Nothing here imports, executes or evaluates the module (NFR-26).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from tdea.analyzer.research.rg10.prepare import (
    HIDDEN_TESTS, PREPARE_MODULE_VERSION, prepare_module, tree_sha256)


@dataclass(frozen=True)
class HarnessPreparation:
    """What the harness records for one output before executing it."""

    prepare_module_version: str
    status: str
    module_text: str | None
    module_sha256: str | None
    tree_sha256: str | None


@dataclass(frozen=True)
class BundleTask:
    """The parts of a bundle task that preparation needs."""

    task_id: str
    variant: Path
    target_module: str
    marker: str


def harness_prepare(bundle: Path, task: BundleTask, output_file: str) -> HarnessPreparation:
    """Prepare one output exactly as the harness would, from the bundle's files."""
    variant_text = (task.variant / task.target_module).read_bytes().decode("utf-8")
    output_text = (bundle / "outputs" / output_file).read_bytes().decode("utf-8")
    prepared = prepare_module(variant_text, task.marker, output_text)
    tree = None
    if prepared.text is not None:
        tree = tree_sha256(task.variant, task.target_module, prepared.text,
                           bundle / "tests" / task.task_id, task.task_id)
    return HarnessPreparation(PREPARE_MODULE_VERSION, prepared.status.value, prepared.text,
                              prepared.module_sha256, tree)


def materialise(bundle: Path, task: BundleTask, module_text: str, destination: Path) -> Path:
    """Lay out the executed tree in ``destination`` as the harness's sandbox copy would."""
    shutil.copytree(task.variant, destination)
    (destination / task.target_module).write_bytes(module_text.encode("utf-8"))
    shutil.copytree(bundle / "tests" / task.task_id,
                    destination / HIDDEN_TESTS / task.task_id)
    return destination

