"""FX-RG10 v1: the synthetic RG10 bundle for RE-14 to RE-16 (Task 8 Day 9, Option A).

SYNTHETIC — not a research-harness bundle and not research data. build() writes a bundle
in the frozen layout (Freeze section 9, SRS 4.5.3) the way the harness would. Its
execution records are made as the harness's preparation step makes them, with TDEA's
public prepare_module and tree_sha256 (rg10_harness_side.harness_prepare); their import
results and test outcomes are invented labels. Nothing here or in any test imports,
executes or evaluates an output or a variant (NFR-26).

Contents:

* Smells T1 and T2 (FR-61's default mapping), each with clean, placebo and injected
  variants, no dose. Every variant holds shop/__init__.py, shop/orders.py and
  tests/test_total.py. orders.py is the RE-15 extraction module (rg10_extraction) with
  both task markers; the placebo variants add a neutral helper above the module-level
  marker, the injected ones a helper carrying the smell (a bare except for T1, a
  .format() call for T2).
* Two tasks on shop/orders.py: task-1 at the module-level marker (two hidden tests),
  task-2 at the marker in class Cart (one hidden test); both declare the project test
  tests/test_total.py::test_total.
* 16 outputs, g01 to g16 (OUTPUTS below): the six RE-15 extraction cases; the seven
  RE-16 cases (syntax error without record, import error, new ``import json``,
  redefinition of total(), clean code, tampered module_sha256, tampered tree_sha256);
  a record missing one hidden test (D-18); an output whose import-time code would write
  a sentinel file; a module nested 10,000 parentheses deep. 11 execution records, one
  per parsing output, with one environment fingerprint.
* Generation metadata with illustrative C9 key names: the names are not frozen, and
  TDEA keeps the metadata whole without checking them (Day 9 decision D9-7, blocked).
  prompt_file is recorded and never read, and prompt files are not part of the frozen
  layout, so none is written.

damaged_copy() makes RE-14's six damaged copies of a built bundle. Every file is written
as explicit UTF-8 bytes with LF line endings, so the bundle is byte-identical on every
platform and run.

Usage: python make_fx_rg10.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from rg10_extraction import (CLASS_MARKER, COUNT, DISCOUNTED, MODULE_MARKER, TARGET_MODULE,
                             VARIANT_MODULE)
from rg10_extraction import OUTPUTS as RE15
from rg10_harness_side import BundleTask, harness_prepare

from tdea.analyzer.research.rg10.prepare import PREPARE_MODULE_VERSION

VERSION = "1"
SENTINEL_VARIABLE = "TDEA_FX_RG10_SENTINEL"
HARNESS_VERSION = "fx-harness 1.0"
ENV_FINGERPRINT = {"image_digest": "sha256:" + "ab" * 32, "python_version": "3.11.9",
                   "pytest_version": "8.3.3", "import_timeout_s": 10, "test_timeout_s": 30,
                   "cpu_limit": 1, "memory_limit_mb": 1024, "network": "none"}
BUNDLE = {"schema_version": "1", "bundle_id": "fx-rg10-v1", "harness_version": HARNESS_VERSION,
          "prepare_module_version": PREPARE_MODULE_VERSION, "env_fingerprint": ENV_FINGERPRINT,
          "created_at": "2026-11-06T09:00:00Z"}

HIDDEN_1 = (".hidden_tests/task-1/test_orders.py::test_discounted",
            ".hidden_tests/task-1/test_orders.py::test_rate_zero")
HIDDEN_2 = (".hidden_tests/task-2/test_cart.py::test_count",)
PROJECT = ("tests/test_total.py::test_total",)
TASKS = (
    {"task_id": "task-1", "project": "shop", "target_module": TARGET_MODULE,
     "marker": MODULE_MARKER, "function_names": ["discounted"], "hidden_tests": list(HIDDEN_1),
     "project_tests": list(PROJECT), "declared_opportunities": {"T1": 2, "T2": 1}},
    {"task_id": "task-2", "project": "shop", "target_module": TARGET_MODULE,
     "marker": CLASS_MARKER, "function_names": ["count"], "hidden_tests": list(HIDDEN_2),
     "project_tests": list(PROJECT), "declared_opportunities": {"T1": 1, "T2": 0}},
)
HIDDEN_TEST_FILES = {
    "task-1": {"test_orders.py": "from shop.orders import discounted\n\n\n"
                                 "def test_discounted():\n"
                                 "    assert discounted([10], 0.5) == 5\n\n\n"
                                 "def test_rate_zero():\n"
                                 "    assert discounted([10], 0) == 10\n"},
    "task-2": {"test_cart.py": "from shop.orders import Cart\n\n\n"
                               "def test_count():\n    assert Cart().count() == 0\n"},
}

PLACEBO_HELPER = ("def _rounded_total(prices):\n"
                  '    """Return the total rounded to cents."""\n'
                  "    return round(total(prices), 2)\n")
T1_HELPER = ("def _safe_total(prices):\n"
             '    """Return the total, or 0 when it cannot be computed."""\n'
             "    try:\n        return total(prices)\n    except:\n        return 0\n")
T2_HELPER = ("def _label(prices):\n"
             '    """Return the total as a label."""\n'
             '    return "total: {}".format(total(prices))\n')


def with_helper(helper: str) -> str:
    """Return the extraction module with ``helper`` above the module-level marker."""
    return VARIANT_MODULE.replace(f"{MODULE_MARKER}\n", f"{helper}\n\n{MODULE_MARKER}\n")


VARIANT_MODULES = {
    ("T1", "clean"): VARIANT_MODULE, ("T1", "placebo"): with_helper(PLACEBO_HELPER),
    ("T1", "injected"): with_helper(T1_HELPER),
    ("T2", "clean"): VARIANT_MODULE, ("T2", "placebo"): with_helper(PLACEBO_HELPER),
    ("T2", "injected"): with_helper(T2_HELPER),
}
PROJECT_TEST = ("from shop.orders import total\n\n\n"
                "def test_total():\n    assert total([1, 2]) == 3\n")


def fenced(code: str) -> str:
    """Return ``code`` as a model output with one python block."""
    return f"Here is the code:\n\n```python\n{code}```\n"


SYNTAX_ERROR = fenced("def discounted(prices, rate)\n    return total(prices) * (1 - rate)\n")
IMPORT_MISSING = fenced("import fx_rg10_absent_dependency\n\n\n"
                        "def discounted(prices, rate):\n"
                        '    """Return the total after a discount."""\n'
                        "    return fx_rg10_absent_dependency.apply(total(prices), rate)\n")
NEW_IMPORT = fenced("import json\n\n\n"
                    "def discounted(prices, rate):\n"
                    '    """Return the discounted total as JSON text."""\n'
                    "    return json.dumps(total(prices) * (1 - rate))\n")
REDEFINITION = fenced("def total(prices):\n"
                      '    """Return the sum of the prices, ignoring None."""\n'
                      "    return sum(price for price in prices if price is not None)\n\n\n"
                      + DISCOUNTED)
SENTINEL = fenced("import os\nimport pathlib\n\n"
                  f'pathlib.Path(os.environ.get("{SENTINEL_VARIABLE}", "fx_rg10_sentinel.txt"))'
                  '.write_text("imported", encoding="utf-8")\n\n\n' + DISCOUNTED)
NESTED = fenced("VALUE = " + "(" * 10000 + "1" + ")" * 10000 + "\n")

ALL_PASS_1 = (("hidden", HIDDEN_1[0], "pass", 0.01), ("hidden", HIDDEN_1[1], "pass", 0.01),
              ("project", PROJECT[0], "pass", 0.02))
ALL_PASS_2 = (("hidden", HIDDEN_2[0], "pass", 0.01), ("project", PROJECT[0], "pass", 0.02))


@dataclass(frozen=True)
class Case:
    """One output: its label, task, variant, text and the harness's (invented) result.

    record is None when the harness writes no record (no module, or one that does not
    parse); otherwise ("ok", tests) or ("error", import error). tamper names a hash the
    record gets wrong on purpose.
    """

    label: str
    task_id: str
    smell: str
    condition: str
    text: str
    record: tuple[str, object] | None
    tamper: str | None = None


CASES = (
    Case("no_block", "task-1", "T1", "clean", RE15["no_block"][1], None),
    Case("one_python", "task-1", "T1", "clean", RE15["one_python"][1], ("ok", ALL_PASS_1)),
    Case("py_then_python3", "task-1", "T2", "clean", RE15["py_then_python3"][1],
         ("ok", (("hidden", HIDDEN_1[0], "fail", 0.03), ("hidden", HIDDEN_1[1], "pass", 0.01),
                 ("project", PROJECT[0], "pass", 0.02)))),
    Case("unterminated", "task-1", "T2", "clean", RE15["unterminated"][1], None),
    Case("only_js", "task-1", "T1", "clean", RE15["only_js"][1], None),
    Case("class_body", "task-2", "T1", "clean", RE15["class_body"][1],
         ("ok", (("hidden", HIDDEN_2[0], "error", 0.0), ("project", PROJECT[0], "timeout", None)))),
    Case("syntax_error", "task-1", "T1", "injected", SYNTAX_ERROR, None),
    Case("import_missing", "task-1", "T1", "placebo", IMPORT_MISSING,
         ("error", "ModuleNotFoundError: No module named 'fx_rg10_absent_dependency'")),
    Case("new_import", "task-1", "T2", "placebo", NEW_IMPORT,
         ("ok", (("hidden", HIDDEN_1[0], "fail", 0.02), ("hidden", HIDDEN_1[1], "fail", 0.02),
                 ("project", PROJECT[0], "pass", 2)))),
    Case("redefinition", "task-1", "T2", "injected", REDEFINITION, ("ok", ALL_PASS_1)),
    Case("clean", "task-2", "T2", "injected", fenced(COUNT), ("ok", ALL_PASS_2)),
    Case("tampered_module", "task-1", "T1", "injected", fenced(DISCOUNTED), ("ok", ALL_PASS_1),
         tamper="module_sha256"),
    Case("tampered_tree", "task-1", "T2", "injected", fenced(DISCOUNTED), ("ok", ALL_PASS_1),
         tamper="tree_sha256"),
    Case("missing_hidden_test", "task-1", "T1", "placebo", fenced(DISCOUNTED),
         ("ok", (ALL_PASS_1[0], ALL_PASS_1[2]))),
    Case("sentinel", "task-1", "T2", "clean", SENTINEL, ("ok", ALL_PASS_1)),
    Case("nested", "task-1", "T1", "clean", NESTED, None),
)
GEN_IDS = {case.label: f"g{number:02d}" for number, case in enumerate(CASES, 1)}

DAMAGED_CASES = ("no_declared_opportunities", "two_outputs_deleted",
                 "other_prepare_module_version", "python_3_12_record",
                 "parsing_output_without_record", "two_env_fingerprints")
DELETED = ("py_then_python3", "clean")  # the outputs two_outputs_deleted removes
OTHER_VERSION = "2" if PREPARE_MODULE_VERSION != "2" else "3"


@dataclass(frozen=True)
class FxRg10:
    """A built FX-RG10 bundle and the gen_id of each labelled output."""

    path: Path
    gen_ids: dict[str, str]
    version: str = VERSION


def write(path: Path, data: bytes | str) -> None:
    """Write ``data`` (text as UTF-8 with LF endings) to ``path``, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def snapshot(root: Path) -> dict[str, bytes]:
    """Return every file under ``root`` by POSIX path, to show nothing was written."""
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(Path(root).rglob("*")) if path.is_file()}


def read_jsonl(path: Path) -> list[dict[str, object]]:
    """Return the JSON objects of a .jsonl file."""
    return [json.loads(line) for line in path.read_bytes().decode("utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """Write one JSON object per line, each line ending in LF."""
    write(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def build(path: Path, cases: tuple[Case, ...] = CASES, meta: dict[str, object] | None = None,
          variant_files: dict[tuple[str, str], dict[str, str]] | None = None,
          version: str = VERSION) -> FxRg10:
    """Build FX-RG10 v1 at ``path``, which must not exist, and return it.

    Later fixture versions (make_fx_rg10_v2) pass more ``cases``, their own bundle.json
    ``meta`` and extra ``variant_files`` ((smell, condition) -> {relative path: text}),
    written before any record is made; the defaults build v1 byte for byte.
    """
    path = Path(path)
    path.mkdir(parents=True)
    gen_ids = {case.label: f"g{number:02d}" for number, case in enumerate(cases, 1)}
    write(path / "bundle.json", json.dumps(BUNDLE if meta is None else meta, indent=2) + "\n")
    for (smell, condition), module in VARIANT_MODULES.items():
        variant = path / "variants" / smell / condition
        write(variant / "shop" / "__init__.py", b"")
        write(variant / TARGET_MODULE, module)
        write(variant / "tests" / "test_total.py", PROJECT_TEST)
        for name, text in (variant_files or {}).get((smell, condition), {}).items():
            write(variant / name, text)
    for task_id, files in HIDDEN_TEST_FILES.items():
        for name, text in files.items():
            write(path / "tests" / task_id / name, text)
    write_jsonl(path / "tasks.jsonl", [dict(task) for task in TASKS])
    manifest, generation, records = [], [], []
    for number, case in enumerate(cases, 1):
        gen_id = gen_ids[case.label]
        manifest.append({"gen_id": gen_id, "prompt_file": f"prompts/{gen_id}.txt",
                         "output_file": f"{gen_id}.md", "task_id": case.task_id,
                         "smell": case.smell, "condition": case.condition, "dose": None,
                         "replicate": 1, "run_order": number})
        write(path / "outputs" / f"{gen_id}.md", case.text)
        generation.append(_generation(gen_id, number, case))
        if case.record is not None:
            records.append(_record(path, gen_id, case))
    write_jsonl(path / "manifest.jsonl", manifest)
    write_jsonl(path / "generation.jsonl", generation)
    write_jsonl(path / "execution.jsonl", records)
    return FxRg10(path, gen_ids, version)


def _generation(gen_id: str, number: int, case: Case) -> dict[str, object]:
    """Return an illustrative C9 generation row (key names not frozen)."""
    return {"gen_id": gen_id, "model_id": "fx-model", "model_version": "fx-2026-10",
            "generated_at": f"2026-11-05T10:{number:02d}:00Z",
            "decoding": {"temperature": 0.2, "top_p": 1.0, "max_tokens": 1024},
            "seed": None if number == 1 else 20261105 + number, "system_prompt": "none",
            "prompt_sha256": hashlib.sha256(f"prompt {gen_id}".encode()).hexdigest(),
            "token_usage": {"prompt": 120, "completion": len(case.text) // 4},
            "retries": 0, "status": "ok"}


def _record(bundle: Path, gen_id: str, case: Case) -> dict[str, object]:
    """Return the execution record the harness would write for one parsing output."""
    task = next(task for task in TASKS if task["task_id"] == case.task_id)
    prepared = harness_prepare(bundle, BundleTask(case.task_id,
                                                  bundle / "variants" / case.smell / case.condition,
                                                  str(task["target_module"]), str(task["marker"])),
                               f"{gen_id}.md")
    assert case.record is not None and prepared.module_sha256 and prepared.tree_sha256
    record: dict[str, object] = {
        "gen_id": gen_id, "module_sha256": prepared.module_sha256,
        "tree_sha256": prepared.tree_sha256, "python_version": "3.11.9",
        "prepare_module_version": prepared.prepare_module_version}
    status, detail = case.record
    if status == "ok":
        record.update(import_status="ok", import_error=None,
                      tests=[{"suite": suite, "test_id": test_id, "outcome": outcome,
                              "duration_s": duration}
                             for suite, test_id, outcome, duration in detail])
    else:
        record.update(import_status="error", import_error=detail)
    if case.tamper is not None:
        digest = str(record[case.tamper])
        record[case.tamper] = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    record.update(env_fingerprint=ENV_FINGERPRINT, executed_at="2026-11-06T08:00:00Z",
                  harness_version=HARNESS_VERSION, tdea_version="0.1.0")
    return record


def damaged_copy(base: Path, case: str, destination: Path) -> Path:
    """Copy the bundle at ``base`` to ``destination`` and apply one RE-14 damage."""
    if case not in DAMAGED_CASES:
        raise ValueError(f"unknown damaged copy {case}")
    shutil.copytree(base, destination)
    if case == "no_declared_opportunities":
        tasks = read_jsonl(destination / "tasks.jsonl")
        del tasks[1]["declared_opportunities"]
        write_jsonl(destination / "tasks.jsonl", tasks)
    elif case == "two_outputs_deleted":
        for label in DELETED:
            (destination / "outputs" / f"{GEN_IDS[label]}.md").unlink()
    elif case == "other_prepare_module_version":
        bundle = json.loads((destination / "bundle.json").read_bytes())
        bundle["prepare_module_version"] = OTHER_VERSION
        write(destination / "bundle.json", json.dumps(bundle, indent=2) + "\n")
    else:
        _damage_records(destination, case)
    return destination


def _damage_records(destination: Path, case: str) -> None:
    """Apply one of the execution-record damages."""
    records = read_jsonl(destination / "execution.jsonl")
    by_label = {label: index for index, record in enumerate(records)
                for label, gen_id in GEN_IDS.items() if record["gen_id"] == gen_id}
    if case == "python_3_12_record":
        records[by_label["one_python"]]["python_version"] = "3.12.4"
    elif case == "parsing_output_without_record":
        del records[by_label["new_import"]]
    else:
        fingerprint = dict(ENV_FINGERPRINT, memory_limit_mb=2048)
        records[by_label["redefinition"]]["env_fingerprint"] = fingerprint
    write_jsonl(destination / "execution.jsonl", records)


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG10 into the given directory and print each output's gen_id."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, gen_id in fixture.gen_ids.items():
        print(f"{gen_id} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
