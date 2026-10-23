"""Preparation, execution and bundle assembly: RG10 pipeline steps 3 to 5 (Freeze section 9).

Inputs are the two folders the earlier harness steps produce:

* materials (step 1): variants/<smell>/<condition>[-<dose>]/, tests/<task_id>/,
  tasks.jsonl and manifest.jsonl, as the frozen bundle layout names them;
* generation (step 2): outputs/ and generation.jsonl. This module does no generation and
  never calls a model.

assemble() copies both into a new bundle folder, then, for each manifest row in file order:

1. Preparation. If the output file exists, build the modified module with TDEA's public
   prepare_module from the variant's target module, and hash the tree about to be
   executed with TDEA's tree_sha256 (Decision 1, condition 1). A missing output, or an
   extraction status of none or unterminated (no module, D-17), gets no record.
2. Execution. Lay the executed tree out in a temporary folder (variant copy, modified
   module as UTF-8 with LF endings, the task's hidden tests under
   .hidden_tests/<task_id>/) and run it in the sandbox: parse, import and, after an "ok"
   import, the declared hidden and project tests. A module that does not parse under the
   sandbox's Python gets no record (FR-59 needs none). A sandbox without a valid reply
   gets no record and is listed in the report; TDEA will list that output as
   unevaluable ("missing execution"). Running the harness again for those outputs is the
   remedy the Freeze names.
3. Bundle. execution.jsonl holds one record per remaining output, with every field of the
   frozen execution-record table; bundle.json holds schema_version "1", the bundle ID,
   the harness and prepare_module versions, the one environment fingerprint and the
   creation time.

Finally the bundle is loaded with TDEA's BundleLoader, which reads it as text only, so a
schema error shows here rather than at evaluation. The bundle is built in a sibling
folder and renamed into place only when complete; an existing destination is refused.
This module imports TDEA's public functions but never imports, runs or evaluates a module
or an output; only the sandbox does that.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rg10_harness import HARNESS_VERSION
from rg10_harness.sandbox import Job, Sandbox
from tdea import __version__ as TDEA_VERSION
from tdea.analyzer.research.rg10.bundle import BundleLoader
from tdea.analyzer.research.rg10.prepare import (HIDDEN_TESTS, PREPARE_MODULE_VERSION,
                                                 prepare_module, tree_sha256)

SCHEMA_VERSION = "1"
MATERIAL_FILES = ("tasks.jsonl", "manifest.jsonl")
GENERATION_FILES = ("generation.jsonl",)


class AssemblyError(Exception):
    """The inputs cannot be assembled into a bundle; nothing was left at the destination."""


@dataclass
class AssemblyReport:
    """What happened to each manifest row."""

    bundle: Path
    recorded: list[str] = field(default_factory=list)
    missing_output: list[str] = field(default_factory=list)
    no_module: list[str] = field(default_factory=list)  # extraction none or unterminated
    not_parsing: list[str] = field(default_factory=list)
    no_reply: list[str] = field(default_factory=list)  # the sandbox gave no valid result


def utc_now() -> str:
    """Return the current UTC time as ISO 8601 text ending in Z, to the second."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def read_jsonl(path: Path) -> list[dict]:
    """Return the JSON objects of a .jsonl file, one per non-empty line."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise AssemblyError(f"{path.name} row {number} is not a JSON object.")
            rows.append(row)
    return rows


def variant_folder(bundle: Path, row: Mapping) -> Path:
    """Return the variant folder a manifest row names: variants/<smell>/<condition>[-<dose>]."""
    name = row["condition"] if row.get("dose") is None else f"{row['condition']}-{row['dose']}"
    return bundle / "variants" / row["smell"] / name


def materialise(bundle: Path, task: Mapping, variant: Path, module_text: str,
                destination: Path) -> Path:
    """Lay out the executed tree that tree_sha256 hashed, in ``destination``."""
    shutil.copytree(variant, destination, symlinks=True)
    (destination / task["target_module"]).write_bytes(module_text.encode("utf-8"))
    shutil.copytree(bundle / "tests" / task["task_id"],
                    destination / HIDDEN_TESTS / task["task_id"], symlinks=True)
    return destination


def declared_tests(task: Mapping) -> tuple[tuple[str, str], ...]:
    """Return the task's declared tests as (suite, test_id), hidden tests first."""
    return (tuple(("hidden", test_id) for test_id in task["hidden_tests"])
            + tuple(("project", test_id) for test_id in task["project_tests"]))


def assemble(materials: Path, generation: Path, destination: Path, sandbox: Sandbox,
             bundle_id: str, *, smell_mapping: Mapping[str, Sequence[str]] | None = None,
             clock: Callable[[], str] = utc_now) -> AssemblyReport:
    """Build the bundle at ``destination`` from the two input folders; see the module notes."""
    destination = Path(destination)
    if destination.exists():
        raise AssemblyError(f"{destination} already exists; choose a new bundle folder.")
    fingerprint = sandbox.fingerprint()  # refuses an unusable sandbox before any copy
    building = destination.with_name(destination.name + ".partial")
    if building.exists():
        shutil.rmtree(building)
    try:
        _copy_inputs(Path(materials), Path(generation), building)
        report = AssemblyReport(destination)
        records = _execute_all(building, sandbox, fingerprint, report, clock)
        _write_jsonl(building / "execution.jsonl", records)
        bundle_json = {"schema_version": SCHEMA_VERSION, "bundle_id": bundle_id,
                       "harness_version": HARNESS_VERSION,
                       "prepare_module_version": PREPARE_MODULE_VERSION,
                       "env_fingerprint": fingerprint, "created_at": clock()}
        (building / "bundle.json").write_text(
            json.dumps(bundle_json, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        BundleLoader(smell_mapping).load(building)  # text-only self-check (FR-58 schema)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    building.rename(destination)
    return report


def _copy_inputs(materials: Path, generation: Path, bundle: Path) -> None:
    """Copy the materials and the generation results into the new bundle folder."""
    for folder, names in ((materials, ("variants", "tests")), (generation, ("outputs",))):
        for name in names:
            if not (folder / name).is_dir():
                raise AssemblyError(f"{folder / name} is missing.")
            shutil.copytree(folder / name, bundle / name, symlinks=True)
    for folder, names in ((materials, MATERIAL_FILES), (generation, GENERATION_FILES)):
        for name in names:
            if not (folder / name).is_file():
                raise AssemblyError(f"{folder / name} is missing.")
            shutil.copyfile(folder / name, bundle / name)


def _execute_all(bundle: Path, sandbox: Sandbox, fingerprint: dict,
                 report: AssemblyReport, clock: Callable[[], str]) -> list[dict]:
    """Prepare and execute every manifest row; return the execution records."""
    tasks = {task["task_id"]: task for task in read_jsonl(bundle / "tasks.jsonl")}
    records = []
    for row in read_jsonl(bundle / "manifest.jsonl"):
        gen_id = row["gen_id"]
        output = bundle / "outputs" / row["output_file"]
        if not output.is_file():
            report.missing_output.append(gen_id)
            continue
        task = tasks.get(row["task_id"])
        if task is None:
            raise AssemblyError(f"manifest row {gen_id} names the unknown task "
                                f"{row['task_id']}.")
        prepared = _prepare(bundle, row, task, output)
        if prepared is None:
            report.no_module.append(gen_id)
            continue
        module_text, module_hash, tree_hash = prepared
        with tempfile.TemporaryDirectory(prefix="rg10-tree-") as scratch:
            tree = materialise(bundle, task, variant_folder(bundle, row), module_text,
                               Path(scratch) / "tree")
            result = sandbox.execute(Job(tree, task["target_module"], declared_tests(task)))
        if result is None:
            report.no_reply.append(gen_id)
            continue
        if not result.get("parses", False):
            report.not_parsing.append(gen_id)
            continue
        records.append({
            "gen_id": gen_id, "module_sha256": module_hash,
            "tree_sha256": tree_hash, "python_version": result["python_version"],
            "prepare_module_version": PREPARE_MODULE_VERSION,
            "import_status": result["import_status"], "import_error": result["import_error"],
            "tests": result["tests"], "env_fingerprint": fingerprint, "executed_at": clock(),
            "harness_version": HARNESS_VERSION, "tdea_version": TDEA_VERSION})
        report.recorded.append(gen_id)
    return records


def _prepare(bundle: Path, row: Mapping, task: Mapping,
             output: Path) -> tuple[str, str, str] | None:
    """Return (module text, module SHA-256, tree SHA-256), or None when there is no module."""
    variant = variant_folder(bundle, row)
    variant_text = (variant / task["target_module"]).read_bytes().decode("utf-8")
    prepared = prepare_module(variant_text, task["marker"], output.read_bytes().decode("utf-8"))
    if prepared.text is None:
        return None
    tree_hash = tree_sha256(variant, task["target_module"], prepared.text,
                            bundle / "tests" / task["task_id"], task["task_id"])
    return prepared.text, prepared.module_sha256, tree_hash


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """Write one JSON object per line, keys sorted, ending in a line break."""
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8")
