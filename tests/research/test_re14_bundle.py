"""RE-14 (FR-58, FR-59), component level: bundle and execution-record validation.

RE-14: "FX-RG10 loads fully; the missing field exits 1 naming task and field; the 2
outputs are listed as missing; the version mismatch and the mixed fingerprints exit 1;
the 3.12 record and the missing record make those outputs unevaluable and the run exits
2". Here the BundleLoader and evaluate_bundle are checked directly: a schema error is a
BundleInvalid whose exit code is 1, and the two records make their outputs unevaluable.
The run itself (``tdea research run`` exiting 1 or 2) needs the RG10 run, which waits for
Day 9 decisions D9-1 and D9-4 (Option B), so RE-14 is not passed here. The remaining
tests check each D9-7 rule of bundle.py with a focused damaged copy. FX-RG10 is
SYNTHETIC; nothing is imported or run (NFR-26).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import make_fx_rg10
import pytest
from make_fx_rg10 import (CASES, DELETED, GEN_IDS, OTHER_VERSION, damaged_copy, read_jsonl,
                          snapshot, write, write_jsonl)

from tdea.analyzer.research.rg10.bundle import (DEFAULT_SMELL_MAPPING, BundleInvalid,
                                                BundleLoader, canonical, checked_mapping,
                                                relative_path, shown)
from tdea.analyzer.research.rg10.execution import (MISSING_EXECUTION, MISSING_OUTPUT, OK,
                                                   PYTHON_MISMATCH, evaluate_bundle)
from tdea.analyzer.research.rg10.prepare import PREPARE_MODULE_VERSION

Change = Callable[[Path], None]
RECORDED = tuple(GEN_IDS[case.label] for case in CASES if case.record is not None)


def evaluability(bundle: Path) -> dict[str, str]:
    """Return each output's evaluability after loading and evaluating ``bundle``."""
    return {evaluation.gen_id: evaluation.match.evaluability
            for evaluation in evaluate_bundle(BundleLoader().load(bundle))}


def copy(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> Path:
    """Return a private copy of FX-RG10 that a test may damage."""
    return shutil.copytree(fx_rg10.path, tmp_path / "copy")


def refusal(bundle: Path, loader: BundleLoader | None = None) -> BundleInvalid:
    """Load ``bundle``, which must be refused, and return the error."""
    with pytest.raises(BundleInvalid) as caught:
        (loader or BundleLoader()).load(bundle)
    return caught.value


# --- RE-14 -------------------------------------------------------------------------------

def test_fx_rg10_loads_completely(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """Every variant, task, output, generation row and record is read; none is missing."""
    bundle = BundleLoader().load(fx_rg10.path)
    assert bundle.meta.bundle_id == "fx-rg10-v1"
    assert bundle.meta.prepare_module_version == PREPARE_MODULE_VERSION
    assert bundle.meta.env_fingerprint == canonical(make_fx_rg10.ENV_FINGERPRINT)
    assert (bundle.meta.schema_version, bundle.meta.harness_version, bundle.meta.created_at) == (
        "1", make_fx_rg10.HARNESS_VERSION, "2026-11-06T09:00:00Z")
    assert sorted(bundle.variants) == sorted((smell, condition, None)
                                             for smell in ("T1", "T2")
                                             for condition in ("clean", "injected", "placebo"))
    assert list(bundle.tasks) == ["task-1", "task-2"]
    assert bundle.tasks["task-2"].declared_opportunities == (("T1", 1), ("T2", 0))
    assert bundle.tasks["task-1"].hidden_tests == make_fx_rg10.HIDDEN_1
    assert [output.gen_id for output in bundle.outputs] == sorted(GEN_IDS.values())
    assert all(output.text is not None and output.generation_metadata is not None
               for output in bundle.outputs)
    assert sorted(bundle.records) == sorted(RECORDED) and len(RECORDED) == 11
    assert not bundle.missing_outputs
    assert bundle.smell_mapping == (("T1", ("W0718", "W0702")), ("T2", ("C0209",)))


def test_variant_file_hashes_and_metadata_are_exact(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """A variant's file hashes equal an independent hash; metadata is the row minus gen_id."""
    bundle = BundleLoader().load(fx_rg10.path)
    variant = bundle.variants[("T1", "injected", None)]
    root = fx_rg10.path / "variants" / "T1" / "injected"
    assert variant.relative == "variants/T1/injected"
    assert variant.file_hashes == tuple(
        (path, hashlib.sha256((root / path).read_bytes()).hexdigest())
        for path in ("shop/__init__.py", "shop/orders.py", "tests/test_total.py"))
    first = read_jsonl(fx_rg10.path / "generation.jsonl")[0]
    del first["gen_id"]
    assert bundle.outputs[0].generation_metadata == canonical(first)
    assert json.loads(bundle.outputs[0].generation_metadata)["seed"] is None


def test_a_task_without_declared_opportunities_is_refused_naming_task_and_field(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """The missing field is a schema error, exit 1, naming the file, row, task and field."""
    error = refusal(damaged_copy(fx_rg10.path, "no_declared_opportunities", tmp_path / "d"))
    assert (error.file, error.row, error.field, error.exit_code) == (
        "tasks.jsonl", 2, "declared_opportunities", 1)
    assert error.cause == ("tasks.jsonl row 2, field declared_opportunities: task 'task-2' "
                           "has no declared_opportunities")
    assert error.error_type == "RG10 bundle invalid" and error.action in str(error)


def test_two_deleted_outputs_are_listed_as_missing(fx_rg10: make_fx_rg10.FxRg10,
                                                   tmp_path: Path) -> None:
    """Missing outputs are kept and listed, never dropped; the other outputs are unchanged."""
    damaged = damaged_copy(fx_rg10.path, "two_outputs_deleted", tmp_path / "d")
    bundle = BundleLoader().load(damaged)
    missing = tuple(sorted(GEN_IDS[label] for label in DELETED))
    assert bundle.missing_outputs == missing
    assert len(bundle.outputs) == len(CASES)
    assert all(output.generation_metadata is not None for output in bundle.outputs)
    expected = evaluability(fx_rg10.path)
    expected.update({gen_id: MISSING_OUTPUT for gen_id in missing})
    assert evaluability(damaged) == expected


def test_another_prepare_module_version_is_refused(fx_rg10: make_fx_rg10.FxRg10,
                                                   tmp_path: Path) -> None:
    """bundle.json's prepare_module_version must equal TDEA's (exit 1)."""
    error = refusal(damaged_copy(fx_rg10.path, "other_prepare_module_version", tmp_path / "d"))
    assert (error.file, error.row, error.field, error.exit_code) == (
        "bundle.json", None, "prepare_module_version", 1)
    assert f"'{OTHER_VERSION}'" in error.reason and f"'{PREPARE_MODULE_VERSION}'" in error.reason


def test_mixed_environment_fingerprints_are_refused(fx_rg10: make_fx_rg10.FxRg10,
                                                    tmp_path: Path) -> None:
    """A record whose fingerprint differs from bundle.json's makes the bundle mixed (exit 1)."""
    error = refusal(damaged_copy(fx_rg10.path, "two_env_fingerprints", tmp_path / "d"))
    row = RECORDED.index(GEN_IDS["redefinition"]) + 1
    assert (error.file, error.row, error.field, error.exit_code) == (
        "execution.jsonl", row, "env_fingerprint", 1)


@pytest.mark.parametrize("case, label, reason", [
    ("python_3_12_record", "one_python", PYTHON_MISMATCH),
    ("parsing_output_without_record", "new_import", MISSING_EXECUTION),
])
def test_a_wrong_or_missing_record_makes_only_that_output_unevaluable(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path, case: str, label: str,
        reason: str) -> None:
    """The bundle loads; that output alone becomes unevaluable with its reason."""
    expected = evaluability(fx_rg10.path)
    assert expected[GEN_IDS[label]] == OK
    expected[GEN_IDS[label]] = reason
    assert evaluability(damaged_copy(fx_rg10.path, case, tmp_path / "d")) == expected


def test_damaged_copy_names_are_checked(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """The fixture refuses an unknown damage instead of returning an undamaged copy."""
    with pytest.raises(ValueError, match="unknown damaged copy"):
        damaged_copy(fx_rg10.path, "no-such-damage", tmp_path / "d")


# --- D9-7 rules, one damaged copy each ----------------------------------------------------

def rows(name: str, change: Callable[[list[dict[str, object]]], None]) -> Change:
    """Return a change that edits the rows of one .jsonl file."""
    def apply(bundle: Path) -> None:
        found = read_jsonl(bundle / name)
        change(found)
        write_jsonl(bundle / name, found)
    return apply


def field(name: str, index: int, key: str, value: object = ...) -> Change:
    """Return a change that sets (or, with no value, deletes) one field of one row."""
    def change(found: list[dict[str, object]]) -> None:
        if value is ...:
            del found[index][key]
        else:
            found[index][key] = value
    return rows(name, change)


def meta(key: str, value: object = ...) -> Change:
    """Return a change that sets (or deletes) one field of bundle.json."""
    def apply(bundle: Path) -> None:
        data = json.loads((bundle / "bundle.json").read_bytes())
        if value is ...:
            del data[key]
        else:
            data[key] = value
        write(bundle / "bundle.json", json.dumps(data))
    return apply


def raw(relative: str, data: bytes | None) -> Change:
    """Return a change that replaces one file's bytes, or removes the file."""
    def apply(bundle: Path) -> None:
        if data is None:
            (bundle / relative).unlink()
        else:
            write(bundle / relative, data)
    return apply


def folder(relative: str) -> Change:
    """Return a change that makes a folder at ``relative``, replacing a file there."""
    def apply(bundle: Path) -> None:
        if (bundle / relative).is_file():
            (bundle / relative).unlink()
        (bundle / relative).mkdir(parents=True)
    return apply


def remove_tree(relative: str) -> Change:
    """Return a change that removes a folder and everything in it."""
    return lambda bundle: shutil.rmtree(bundle / relative)


def replace_in(relative: str, old: str, new: str) -> Change:
    """Return a change that replaces text in one bundle file."""
    def apply(bundle: Path) -> None:
        text = (bundle / relative).read_bytes().decode("utf-8")
        write(bundle / relative, text.replace(old, new))
    return apply


ORDERS = "variants/T1/clean/shop/orders.py"
FINGERPRINT_FLOAT = dict(make_fx_rg10.ENV_FINGERPRINT, cpu_limit=1.0)
DAMAGES: dict[str, tuple[Change, tuple[str, int | None, str | None]]] = {
    # bundle.json
    "bundle.json missing": (raw("bundle.json", None), ("bundle.json", None, None)),
    "bundle.json a folder": (folder("bundle.json"), ("bundle.json", None, None)),
    "bundle.json not JSON": (raw("bundle.json", b"{"), ("bundle.json", None, None)),
    "bundle.json an array": (raw("bundle.json", b"[]"), ("bundle.json", None, None)),
    "bundle.json not UTF-8": (raw("bundle.json", b"\xff{}"), ("bundle.json", None, None)),
    "bundle.json repeated key": (raw("bundle.json", b'{"a": 1, "a": 2}'),
                                 ("bundle.json", None, None)),
    "no schema_version": (meta("schema_version"), ("bundle.json", None, "schema_version")),
    "no bundle_id": (meta("bundle_id"), ("bundle.json", None, "bundle_id")),
    "no harness_version": (meta("harness_version"), ("bundle.json", None, "harness_version")),
    "no prepare_module_version": (meta("prepare_module_version"),
                                  ("bundle.json", None, "prepare_module_version")),
    "no env_fingerprint": (meta("env_fingerprint"), ("bundle.json", None, "env_fingerprint")),
    "no created_at": (meta("created_at"), ("bundle.json", None, "created_at")),
    "empty schema_version": (meta("schema_version", ""), ("bundle.json", None, "schema_version")),
    "created_at a number": (meta("created_at", 5), ("bundle.json", None, "created_at")),
    "env_fingerprint a list": (meta("env_fingerprint", []),
                               ("bundle.json", None, "env_fingerprint")),
    "network host": (meta("env_fingerprint", dict(make_fx_rg10.ENV_FINGERPRINT, network="host")),
                     ("bundle.json", None, "env_fingerprint")),
    "no network": (meta("env_fingerprint", {"image_digest": "x"}),
                   ("bundle.json", None, "env_fingerprint")),
    # variants
    "variants missing": (remove_tree("variants"), ("variants", None, None)),
    "a file in variants": (raw("variants/README.txt", b"x"), ("variants/README.txt", None, None)),
    "a badly named variant": (folder("variants/T1/dirty"), ("variants/T1/dirty", None, None)),
    "a dose without a value": (folder("variants/T1/injected-"),
                               ("variants/T1/injected-", None, None)),
    "a missing condition": (remove_tree("variants/T2/placebo"), ("variants/T2", None, None)),
    "hidden tests in a variant": (raw("variants/T1/clean/.hidden_tests/x.py", b""),
                                  ("variants/T1/clean/.hidden_tests", None, None)),
    # tasks
    "tasks.jsonl missing": (raw("tasks.jsonl", None), ("tasks.jsonl", None, None)),
    "tests missing": (remove_tree("tests"), ("tests", None, None)),
    "tasks.jsonl not UTF-8": (raw("tasks.jsonl", b"\xff\n"), ("tasks.jsonl", None, None)),
    "a task row not an object": (rows("tasks.jsonl", lambda found: found.append([1])),
                                 ("tasks.jsonl", 3, None)),
    "a blank task line": (replace_in("tasks.jsonl", "}\n{", "}\n\n{"), ("tasks.jsonl", 2, None)),
    "NaN in a task": (replace_in("tasks.jsonl", '"T1": 2', '"T1": NaN'), ("tasks.jsonl", 1, None)),
    "1e400 in a task": (replace_in("tasks.jsonl", '"T1": 2', '"T1": 1e400'),
                        ("tasks.jsonl", 1, None)),
    "no task_id": (field("tasks.jsonl", 0, "task_id"), ("tasks.jsonl", 1, "task_id")),
    "a task_id with a slash": (field("tasks.jsonl", 0, "task_id", "a/b"),
                               ("tasks.jsonl", 1, "task_id")),
    "a task_id of a dot": (field("tasks.jsonl", 0, "task_id", "."), ("tasks.jsonl", 1, "task_id")),
    "a repeated task_id": (field("tasks.jsonl", 1, "task_id", "task-1"),
                           ("tasks.jsonl", 2, "task_id")),
    "an empty project": (field("tasks.jsonl", 0, "project", ""), ("tasks.jsonl", 1, "project")),
    "an absolute target": (field("tasks.jsonl", 0, "target_module", "/shop/orders.py"),
                           ("tasks.jsonl", 1, "target_module")),
    "a target leaving the variant": (field("tasks.jsonl", 0, "target_module", "../orders.py"),
                                     ("tasks.jsonl", 1, "target_module")),
    "a target with a backslash": (field("tasks.jsonl", 0, "target_module", "shop\\orders.py"),
                                  ("tasks.jsonl", 1, "target_module")),
    "a two-line marker": (field("tasks.jsonl", 0, "marker", "# a\n# b"),
                          ("tasks.jsonl", 1, "marker")),
    "a marker with a CR": (field("tasks.jsonl", 0, "marker", "# a\r# b"),
                           ("tasks.jsonl", 1, "marker")),
    "function_names a string": (field("tasks.jsonl", 0, "function_names", "discounted"),
                                ("tasks.jsonl", 1, "function_names")),
    "a repeated hidden test": (field("tasks.jsonl", 1, "hidden_tests", ["a", "a"]),
                               ("tasks.jsonl", 2, "hidden_tests")),
    "a project test not a string": (field("tasks.jsonl", 0, "project_tests", [1]),
                                    ("tasks.jsonl", 1, "project_tests")),
    "declared opportunities a list": (field("tasks.jsonl", 0, "declared_opportunities", [2, 1]),
                                      ("tasks.jsonl", 1, "declared_opportunities")),
    "a declared type the mapping lacks": (
        field("tasks.jsonl", 0, "declared_opportunities", {"T1": 2, "T2": 1, "T3": 0}),
        ("tasks.jsonl", 1, "declared_opportunities")),
    "a mapping type not declared": (field("tasks.jsonl", 0, "declared_opportunities", {"T1": 2}),
                                    ("tasks.jsonl", 1, "declared_opportunities")),
    "a negative count": (field("tasks.jsonl", 0, "declared_opportunities", {"T1": -1, "T2": 1}),
                         ("tasks.jsonl", 1, "declared_opportunities")),
    "a boolean count": (field("tasks.jsonl", 0, "declared_opportunities", {"T1": True, "T2": 1}),
                        ("tasks.jsonl", 1, "declared_opportunities")),
    "a fractional count": (field("tasks.jsonl", 0, "declared_opportunities",
                                 {"T1": 2, "T2": 1.5}),
                           ("tasks.jsonl", 1, "declared_opportunities")),
    "a task's tests missing": (remove_tree("tests/task-2"), ("tests/task-2", None, None)),
    # manifest
    "manifest.jsonl missing": (raw("manifest.jsonl", None), ("manifest.jsonl", None, None)),
    "a repeated gen_id": (field("manifest.jsonl", 1, "gen_id", "g01"),
                          ("manifest.jsonl", 2, "gen_id")),
    "no prompt_file": (field("manifest.jsonl", 0, "prompt_file"),
                       ("manifest.jsonl", 1, "prompt_file")),
    "an output_file leaving outputs": (field("manifest.jsonl", 0, "output_file", "../g01.md"),
                                       ("manifest.jsonl", 1, "output_file")),
    "an absolute output_file": (field("manifest.jsonl", 0, "output_file", "/g01.md"),
                                ("manifest.jsonl", 1, "output_file")),
    "an output_file with a drive": (field("manifest.jsonl", 0, "output_file", "C:g01.md"),
                                    ("manifest.jsonl", 1, "output_file")),
    "an unknown task": (field("manifest.jsonl", 0, "task_id", "task-9"),
                        ("manifest.jsonl", 1, "task_id")),
    "a smell the mapping lacks": (field("manifest.jsonl", 0, "smell", "T3"),
                                  ("manifest.jsonl", 1, "smell")),
    "an unknown condition": (field("manifest.jsonl", 0, "condition", "dirty"),
                             ("manifest.jsonl", 1, "condition")),
    "a numeric dose": (field("manifest.jsonl", 0, "dose", 2), ("manifest.jsonl", 1, "dose")),
    "an empty dose": (field("manifest.jsonl", 0, "dose", ""), ("manifest.jsonl", 1, "dose")),
    "no dose": (field("manifest.jsonl", 0, "dose"), ("manifest.jsonl", 1, "dose")),
    "a dose with no variant": (field("manifest.jsonl", 0, "dose", "2"),
                               ("manifest.jsonl", 1, "condition")),
    "a text replicate": (field("manifest.jsonl", 0, "replicate", "1"),
                         ("manifest.jsonl", 1, "replicate")),
    "a boolean run_order": (field("manifest.jsonl", 0, "run_order", True),
                            ("manifest.jsonl", 1, "run_order")),
    # markers and target modules
    "a marker missing from a variant": (replace_in(ORDERS, make_fx_rg10.MODULE_MARKER, "# gone"),
                                        ("tasks.jsonl", 1, "marker")),
    "a marker twice in a variant": (replace_in(ORDERS, "class Cart:",
                                               make_fx_rg10.MODULE_MARKER + "\nclass Cart:"),
                                    ("tasks.jsonl", 1, "marker")),
    "a target module missing": (raw("variants/T2/injected/shop/orders.py", None),
                                ("tasks.jsonl", 1, "target_module")),
    "a variant module not UTF-8": (raw(ORDERS, b"\xff"), (ORDERS, None, None)),
    # generation metadata
    "generation.jsonl missing": (raw("generation.jsonl", None), ("generation.jsonl", None, None)),
    "a generation row without gen_id": (field("generation.jsonl", 0, "gen_id"),
                                        ("generation.jsonl", 1, "gen_id")),
    "a generation row for no output": (field("generation.jsonl", 0, "gen_id", "g99"),
                                       ("generation.jsonl", 1, "gen_id")),
    "a repeated generation row": (field("generation.jsonl", 1, "gen_id", "g01"),
                                  ("generation.jsonl", 2, "gen_id")),
    # execution records, bundle level
    "execution.jsonl missing": (raw("execution.jsonl", None), ("execution.jsonl", None, None)),
    "a record for no output": (field("execution.jsonl", 0, "gen_id", "g99"),
                               ("execution.jsonl", 1, "gen_id")),
    "a repeated record": (field("execution.jsonl", 1, "gen_id", RECORDED[0]),
                          ("execution.jsonl", 2, "gen_id")),
    "a record without fingerprint": (field("execution.jsonl", 0, "env_fingerprint"),
                                     ("execution.jsonl", 1, "env_fingerprint")),
    "a fingerprint with 1.0 for 1": (field("execution.jsonl", 0, "env_fingerprint",
                                           FINGERPRINT_FLOAT),
                                     ("execution.jsonl", 1, "env_fingerprint")),
    # outputs
    "outputs missing": (remove_tree("outputs"), ("outputs", None, None)),
    "an output not UTF-8": (raw("outputs/g02.md", b"```python\n\xff\n```\n"),
                            ("outputs/g02.md", None, None)),
    "an output that is a folder": (folder("outputs/g02.md"), ("outputs/g02.md", None, None)),
}


@pytest.mark.parametrize("name", list(DAMAGES))
def test_each_schema_error_names_file_row_and_field(fx_rg10: make_fx_rg10.FxRg10,
                                                    tmp_path: Path, name: str) -> None:
    """D9-7: each damage is refused with exit 1, naming exactly its file, row and field."""
    change, where = DAMAGES[name]
    bundle = copy(fx_rg10, tmp_path)
    change(bundle)
    error = refusal(bundle)
    assert (error.file, error.row, error.field) == where, error.cause
    assert error.exit_code == 1 and error.cause.startswith(where[0])


def test_a_bundle_folder_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    """A path that is not a folder cannot be a bundle."""
    error = refusal(tmp_path / "absent")
    assert error.file == str(tmp_path / "absent") and error.row is None


@pytest.mark.parametrize("kind", ["link in a variant", "link as an output", "link as tests"])
def test_symbolic_links_are_refused(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path,
                                    kind: str) -> None:
    """A symbolic link is never followed: it is a schema error where it is read."""
    bundle = copy(fx_rg10, tmp_path)
    target = bundle / "variants" / "T1" / "clean" / "shop" / "orders.py"
    try:
        if kind == "link in a variant":
            (bundle / "variants" / "T1" / "clean" / "link.py").symlink_to(target)
        elif kind == "link as an output":
            (bundle / "outputs" / "g02.md").unlink()
            (bundle / "outputs" / "g02.md").symlink_to(bundle / "outputs" / "g03.md")
        else:
            shutil.rmtree(bundle / "tests" / "task-2")
            (bundle / "tests" / "task-2").symlink_to(bundle / "tests" / "task-1",
                                                     target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot create symbolic links")
    error = refusal(bundle)
    assert (error.file, error.reason) == {
        "link in a variant": ("variants/T1/clean/link.py",
                              "is a symbolic link, which a bundle may not contain"),
        "link as an output": ("outputs/g02.md", "is not a regular file"),
        "link as tests": ("tests/task-2", "is not a folder")}[kind]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need POSIX")
def test_a_named_pipe_in_a_variant_is_refused_without_reading_it(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """A special file is refused before any read, which on a pipe would block."""
    bundle = copy(fx_rg10, tmp_path)
    os.mkfifo(bundle / "variants" / "T2" / "clean" / "pipe")
    error = refusal(bundle)
    assert (error.file, error.reason) == ("variants/T2/clean/pipe",
                                          "is neither a regular file nor a folder")


# --- accepted boundaries -----------------------------------------------------------------

def test_empty_record_files_and_a_dose_variant_load(fx_rg10: make_fx_rg10.FxRg10,
                                                    tmp_path: Path) -> None:
    """No records loads (parsing outputs become unevaluable); a dose names its variant."""
    bundle = copy(fx_rg10, tmp_path)
    write(bundle / "execution.jsonl", b"")
    shutil.copytree(bundle / "variants" / "T1" / "injected",
                                 bundle / "variants" / "T1" / "injected-2")
    field("manifest.jsonl", 6, "dose", "2")(bundle)
    loaded = BundleLoader().load(bundle)
    assert not loaded.records
    assert ("T1", "injected", "2") in loaded.variants
    assert loaded.variants[("T1", "injected", "2")].relative == "variants/T1/injected-2"
    assert loaded.outputs[6].variant_key == ("T1", "injected", "2")
    found = {evaluation.gen_id: evaluation.match.evaluability
             for evaluation in evaluate_bundle(loaded)}
    assert {found[gen_id] for gen_id in RECORDED} == {MISSING_EXECUTION}


def test_tolerated_layout_details_load(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """CRLF lines, no final line break, unknown keys and a missing generation row load."""
    bundle = copy(fx_rg10, tmp_path)
    text = (bundle / "tasks.jsonl").read_bytes().decode("utf-8")
    write(bundle / "tasks.jsonl", text.replace("\n", "\r\n"))
    write(bundle / "manifest.jsonl",
          (bundle / "manifest.jsonl").read_bytes().decode("utf-8").rstrip("\n"))
    field("manifest.jsonl", 0, "note", "unknown keys are not checked")(bundle)
    rows("generation.jsonl", lambda found: found.pop(0))(bundle)
    loaded = BundleLoader().load(bundle)
    assert len(loaded.tasks) == 2 and len(loaded.outputs) == len(CASES)
    assert loaded.outputs[0].generation_metadata is None
    assert loaded.outputs[1].generation_metadata is not None


def test_outputs_follow_gen_id_order_whatever_the_manifest_order(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """Outputs, and so evaluations, are ordered by gen_id, not by manifest line."""
    bundle = copy(fx_rg10, tmp_path)
    rows("manifest.jsonl", lambda found: found.reverse())(bundle)
    loaded = BundleLoader().load(bundle)
    assert [output.gen_id for output in loaded.outputs] == sorted(GEN_IDS.values())
    assert loaded.outputs[0].row == len(CASES)
    assert evaluate_bundle(loaded) == evaluate_bundle(BundleLoader().load(fx_rg10.path))


def test_another_smell_mapping_changes_what_is_required(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """The declared types must match the mapping in use, not the default one."""
    error = refusal(fx_rg10.path, BundleLoader({"T1": ["W0718", "W0702"]}))
    assert (error.file, error.row, error.field) == ("tasks.jsonl", 1, "declared_opportunities")
    assert "'T2'" in error.reason
    custom = BundleLoader({"T2": ("C0209",), "T1": ["W0702"]}).load(fx_rg10.path)
    assert custom.smell_mapping == (("T1", ("W0702",)), ("T2", ("C0209",)))


@pytest.mark.parametrize("mapping", [{}, [("T1", ["W0718"])], {"T1": "W0718"}, {"T1": []},
                                     {"": ["W0718"]}, {"T1": [""]}, {"T1": [7]}, {1: ["W0718"]}])
def test_a_malformed_smell_mapping_is_a_programming_error(mapping: object) -> None:
    """The run file validates the mapping (Option B); the loader refuses a bad one outright."""
    with pytest.raises(ValueError):
        checked_mapping(mapping)


def test_the_default_mapping_is_fr61s() -> None:
    """FR-61: T1 = W0718 + W0702, T2 = C0209."""
    assert DEFAULT_SMELL_MAPPING == {"T1": ("W0718", "W0702"), "T2": ("C0209",)}


@pytest.mark.parametrize("value, expected", [
    ("g01.md", "g01.md"), ("sub/g01.md", "sub/g01.md"), ("./g01.md", "g01.md"), ("..", None),
    ("a/../b", None), ("/a", None), ("a\\b", None), ("c:a", None), ("a\tb", None), (".", None),
])
def test_relative_paths(value: str, expected: str | None) -> None:
    """Bundle paths are relative POSIX paths that stay inside their folder."""
    path = relative_path(value)
    assert (None if path is None else path.as_posix()) == expected


def test_shown_quotes_escapes_and_shortens() -> None:
    """IDs in messages are quoted, escaped and at most 60 characters."""
    assert shown("task-1") == "'task-1'"
    assert shown("a\nb") == "'a\\nb'"
    assert len(shown("x" * 100)) == 60 and shown("x" * 100).endswith("...")
    assert shown("x" * 58) == "'" + "x" * 58 + "'"


# --- determinism and no writes -----------------------------------------------------------

def test_loading_is_deterministic_and_writes_nothing(fx_rg10: make_fx_rg10.FxRg10,
                                                     tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Two loads and evaluations are equal; the bundle and the working folder stay as they were."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    before = snapshot(fx_rg10.path)
    first = BundleLoader().load(fx_rg10.path)
    assert first == BundleLoader().load(fx_rg10.path)
    assert evaluate_bundle(first) == evaluate_bundle(BundleLoader().load(fx_rg10.path))
    assert snapshot(fx_rg10.path) == before
    assert not any(work.iterdir())


def test_a_refused_bundle_is_left_unchanged(fx_rg10: make_fx_rg10.FxRg10,
                                            tmp_path: Path) -> None:
    """A schema error writes nothing: the damaged copy is byte-identical afterwards."""
    damaged = damaged_copy(fx_rg10.path, "two_env_fingerprints", tmp_path / "d")
    before = snapshot(damaged)
    refusal(damaged)
    assert snapshot(damaged) == before


# --- unreadable parts are refused, never skipped -----------------------------------------

def failing(real: Callable[..., object], name: str, after: int = 0) -> Callable[..., object]:
    """Wrap an os or Path function: PermissionError for paths named ``name``.

    The first ``after`` such calls succeed, so a file can be hashed and then fail to read.
    """
    seen = []

    def wrapper(path: object, *args: object, **kwargs: object) -> object:
        if Path(str(path)).as_posix().endswith(name):
            seen.append(path)
            if len(seen) > after:
                raise PermissionError(13, "Permission denied", str(path))
        return real(path, *args, **kwargs)
    return wrapper


@pytest.mark.parametrize("target, name, after, where", [
    ("read_bytes", "bundle.json", 0, "bundle.json"),
    ("read_bytes", "T1/clean/shop/orders.py", 0, "variants/T1/clean"),
    ("read_bytes", "T1/clean/shop/orders.py", 1, ORDERS),
    ("read_bytes", "g02.md", 0, "outputs/g02.md"),
    ("lstat", "tasks.jsonl", 0, "tasks.jsonl"),
    ("listdir", "variants/T2", 0, "variants/T2"),
    ("scandir", "T1/clean/shop", 0, "variants/T1/clean"),
])
def test_unreadable_parts_are_refused(fx_rg10: make_fx_rg10.FxRg10,
                                      monkeypatch: pytest.MonkeyPatch, target: str, name: str,
                                      after: int, where: str) -> None:
    """An OS error while reading is a schema error naming the part, not a skipped part."""
    owner = Path if target == "read_bytes" else os
    monkeypatch.setattr(owner, target, failing(getattr(owner, target), name, after))
    error = refusal(fx_rg10.path)
    assert (error.file, error.reason) == (where, "cannot be read")


def test_a_file_name_that_is_not_utf8_is_refused(fx_rg10: make_fx_rg10.FxRg10,
                                                 tmp_path: Path) -> None:
    """A name that cannot be written as UTF-8 cannot be recorded or hashed: refused."""
    bundle = copy(fx_rg10, tmp_path)
    try:
        descriptor = os.open(os.fsencode(bundle / "variants" / "T1" / "clean") + b"/\xff.py",
                             os.O_CREAT | os.O_WRONLY)
    except (OSError, ValueError):
        pytest.skip("this file system cannot hold a name that is not UTF-8")
    os.close(descriptor)
    error = refusal(bundle)
    assert error.reason == "has a file name that is not UTF-8"
    assert error.file.startswith("variants/T1/clean/")
