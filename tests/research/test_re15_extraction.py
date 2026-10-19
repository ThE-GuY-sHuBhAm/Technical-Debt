"""RE-15 (FR-59): code extraction and prepare_module parity (Day 3 part, Day 9 part).

Steps: 1) evaluate the six outputs; 2) read status and inserted range; 3) check that the
variant file is unchanged; 4) prepare each output as the harness does and compare text
and SHA-256. Expected module texts are written out by hand from the fixture, and the tree
hash is checked against the files of a materialised executed tree hashed independently
here. Outputs are inert text: nothing is imported or run (NFR-26).

Day 9 adds the same steps through the bundle: FX-RG10's six extraction outputs are loaded
by the BundleLoader and evaluated by evaluate_bundle, the bundle stays byte-identical, and
every untampered execution record (made by the harness stand-in) carries the module and
tree SHA-256 that TDEA's evaluation rebuilds.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import os
from pathlib import Path

import make_fx_rg10
import pytest
from rg10_extraction import (CLASS_MARKER, COUNT, DISCOUNTED, MODULE_MARKER, OUTPUTS,
                             TARGET_MODULE, VARIANT_MODULE)
from rg10_harness_side import BundleTask, harness_prepare, materialise

from tdea.analyzer.research.rg10.bundle import BundleLoader
from tdea.analyzer.research.rg10.execution import evaluate_bundle
from tdea.analyzer.research.rg10.prepare import (
    PREPARE_MODULE_VERSION, ExtractionStatus, prepare_module, tree_sha256)

TWO_LINE = "def discounted(prices, rate):\n    return total(prices) * (1 - rate)\n"
INDENTED_COUNT = "".join(f"    {line}\n" for line in COUNT.splitlines())
EXPECTED = {  # name -> (status, module text, inserted range)
    "no_block": (ExtractionStatus.NONE, None, None),
    "one_python": (ExtractionStatus.INSERTED,
                   VARIANT_MODULE.replace(f"{MODULE_MARKER}\n", f"{MODULE_MARKER}\n{DISCOUNTED}"),
                   (10, 12)),
    "py_then_python3": (ExtractionStatus.MULTIPLE,
                        VARIANT_MODULE.replace(f"{MODULE_MARKER}\n",
                                               f"{MODULE_MARKER}\n{TWO_LINE}"),
                        (10, 11)),
    "unterminated": (ExtractionStatus.UNTERMINATED, None, None),
    "only_js": (ExtractionStatus.NONE, None, None),
    "class_body": (ExtractionStatus.INSERTED,
                   VARIANT_MODULE.replace(f"    {CLASS_MARKER}\n",
                                          f"    {CLASS_MARKER}\n{INDENTED_COUNT}"),
                   (20, 22)),
}
TASK_IDS = {MODULE_MARKER: "task-1", CLASS_MARKER: "task-2"}


@pytest.fixture(name="bundle")
def _bundle(tmp_path: Path) -> Path:
    """Write a minimal bundle: one variant, two tasks' hidden tests and the outputs."""
    bundle = tmp_path / "bundle"
    variant = bundle / "variants" / "T1" / "injected"
    files = {
        variant / TARGET_MODULE: VARIANT_MODULE.encode("utf-8"),
        variant / "shop" / "__init__.py": b"",
        variant / "README.txt": b"Variant notes\r\nwith CRLF endings\r\n",
        variant / "assets" / "logo.bin": bytes(range(64)),
        bundle / "tests" / "task-1" / "test_orders.py": b"def test_discounted():\n    pass\n",
        bundle / "tests" / "task-2" / "test_cart.py": b"def test_count():\n    pass\n",
    }
    for name, (_marker, text) in OUTPUTS.items():
        files[bundle / "outputs" / f"{name}.md"] = text.encode("utf-8")
    files[bundle / "outputs" / "one_python_crlf.md"] = (
        OUTPUTS["one_python"][1].replace("\n", "\r\n").encode("utf-8"))
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return bundle


def _task(bundle: Path, marker: str) -> BundleTask:
    """Return the task whose marker is ``marker``."""
    return BundleTask(TASK_IDS[marker], bundle / "variants" / "T1" / "injected", TARGET_MODULE,
                      marker)


def hand_tree_hash(root: Path) -> str:
    """Hash a materialised tree directly: sorted 'path TAB sha256' lines, each ending in LF."""
    lines = sorted(f"{path.relative_to(root).as_posix()}\t"
                   f"{hashlib.sha256(path.read_bytes()).hexdigest()}"
                   for path in root.rglob("*") if path.is_file())
    return hashlib.sha256("".join(f"{line}\n" for line in lines).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", list(OUTPUTS))
def test_extraction_status_text_and_range(name: str) -> None:
    """Steps 1-2: none, inserted, multiple (first used), unterminated, none; class indent."""
    marker, output = OUTPUTS[name]
    status, text, span = EXPECTED[name]
    prepared = prepare_module(VARIANT_MODULE, marker, output)
    assert (prepared.status, prepared.text, prepared.inserted_range) == (status, text, span)
    assert prepared.n_added_lines == (0 if span is None else span[1] - span[0] + 1)
    assert prepared.version == PREPARE_MODULE_VERSION
    expected_sha = None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert prepared.module_sha256 == expected_sha


def test_variant_file_is_unchanged(bundle: Path) -> None:
    """Step 3: preparing every output leaves the variant's files byte-for-byte unchanged."""
    variant = bundle / "variants" / "T1" / "injected"
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in variant.rglob("*") if path.is_file()}
    for name, (marker, _output) in OUTPUTS.items():
        harness_prepare(bundle, _task(bundle, marker), f"{name}.md")
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in variant.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("name", list(OUTPUTS))
def test_harness_side_parity(bundle: Path, tmp_path: Path, name: str) -> None:
    """Step 4: the harness-side call gives the same text and SHA-256 as TDEA's."""
    marker, output = OUTPUTS[name]
    task = _task(bundle, marker)
    harness = harness_prepare(bundle, task, f"{name}.md")
    tdea_side = prepare_module(VARIANT_MODULE, marker, output)
    assert harness.prepare_module_version == PREPARE_MODULE_VERSION
    assert (harness.status, harness.module_text, harness.module_sha256) == (
        tdea_side.status.value, tdea_side.text, tdea_side.module_sha256)
    if tdea_side.text is None:
        assert harness.tree_sha256 is None
        return
    executed = materialise(bundle, task, tdea_side.text, tmp_path / "executed")
    assert harness.tree_sha256 == hand_tree_hash(executed)
    assert tree_sha256(task.variant, TARGET_MODULE, tdea_side.text,
                       bundle / "tests" / task.task_id, task.task_id) == harness.tree_sha256


def test_crlf_output_gives_the_same_module(bundle: Path) -> None:
    """An output saved with CRLF endings gives the same LF module and hashes."""
    task = _task(bundle, MODULE_MARKER)
    crlf = harness_prepare(bundle, task, "one_python_crlf.md")
    assert crlf == harness_prepare(bundle, task, "one_python.md")
    assert "\r" not in crlf.module_text


def test_tree_hash_covers_every_part_of_the_executed_tree(bundle: Path) -> None:
    """Changing a hidden test, a variant file or the module changes the tree hash."""
    task = _task(bundle, MODULE_MARKER)
    text = EXPECTED["one_python"][1]
    tests = bundle / "tests" / task.task_id

    def current(module_text: str = text) -> str:
        """Return the tree hash for the bundle as it is now."""
        return tree_sha256(task.variant, TARGET_MODULE, module_text, tests, task.task_id)

    baseline = current()
    assert current(text.replace("\n", "\r\n")) == baseline  # the module is hashed with LF
    assert current(text + "# tampered\n") != baseline
    (tests / "test_orders.py").write_bytes(b"def test_discounted():\n    assert True\n")
    changed_test = current()
    (task.variant / "README.txt").write_bytes(b"Variant notes\nwith LF endings\n")
    assert len({baseline, changed_test, current()}) == 3


def test_prepare_module_is_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    """prepare_module opens no file and gives the same result every time."""
    first = prepare_module(VARIANT_MODULE, *OUTPUTS["one_python"])

    def no_files(*_args: object, **_kwargs: object) -> None:
        """Fail any attempt to open a file."""
        raise AssertionError("prepare_module opened a file")

    for module, name in ((builtins, "open"), (io, "open"), (os, "open")):
        monkeypatch.setattr(module, name, no_files)
    assert prepare_module(VARIANT_MODULE, *OUTPUTS["one_python"]) == first


@pytest.mark.parametrize("marker", ["# not in the module", "", "\n", "Cart"])
def test_marker_must_occur_exactly_once(marker: str) -> None:
    """A marker that is missing, empty, multi-line or not unique is refused."""
    module = VARIANT_MODULE + "# Cart again\n" if marker == "Cart" else VARIANT_MODULE
    with pytest.raises(ValueError):
        prepare_module(module, marker, OUTPUTS["one_python"][1])


def test_symbolic_links_in_the_tree_are_refused(bundle: Path) -> None:
    """tree_sha256 refuses a symbolic link rather than guess how to hash it."""
    task = _task(bundle, MODULE_MARKER)
    try:
        (task.variant / "link.py").symlink_to(task.variant / TARGET_MODULE)
    except OSError:
        pytest.skip("this platform cannot create symbolic links")
    with pytest.raises(ValueError, match="symbolic links"):
        tree_sha256(task.variant, TARGET_MODULE, EXPECTED["one_python"][1],
                    bundle / "tests" / task.task_id, task.task_id)


# --- Day 9: the same steps through FX-RG10 and evaluate_bundle --------------------------

@pytest.mark.parametrize("name", list(OUTPUTS))
def test_bundle_evaluation_gives_the_extraction_results(fx_rg10: make_fx_rg10.FxRg10,
                                                        name: str) -> None:
    """Steps 1-2 through the bundle: status, range, added lines and the module's SHA-256."""
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(fx_rg10.path))}
    evaluation = found[make_fx_rg10.GEN_IDS[name]]
    status, text, span = EXPECTED[name]
    assert (evaluation.extraction_status, evaluation.inserted_range) == (status.value, span)
    assert evaluation.n_added_lines == (0 if span is None else span[1] - span[0] + 1)
    assert evaluation.module_sha256 == (
        None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest())
    if text is None:  # D-17: no module, so nothing to parse or hash, and both suites fail
        assert (evaluation.parses, evaluation.tree_sha256) == (None, None)
        assert (evaluation.match.insertable, evaluation.match.suite_result_source) == (
            False, "rule")


def test_bundle_evaluation_leaves_the_bundle_unchanged(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """Step 3 through the bundle: every variant and output file is byte-identical after."""
    before = make_fx_rg10.snapshot(fx_rg10.path)
    evaluate_bundle(BundleLoader().load(fx_rg10.path))
    assert make_fx_rg10.snapshot(fx_rg10.path) == before


def test_harness_records_carry_tdeas_hashes(fx_rg10: make_fx_rg10.FxRg10) -> None:
    """Step 4 through the bundle: each untampered record has TDEA's module and tree SHA-256."""
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(fx_rg10.path))}
    tampered = {make_fx_rg10.GEN_IDS[case.label] for case in make_fx_rg10.CASES if case.tamper}
    records = make_fx_rg10.read_jsonl(fx_rg10.path / "execution.jsonl")
    checked = [record["gen_id"] for record in records if record["gen_id"] not in tampered]
    assert len(checked) == 9
    for record in records:
        if record["gen_id"] not in tampered:
            evaluation = found[str(record["gen_id"])]
            assert (record["module_sha256"], record["tree_sha256"]) == (
                evaluation.module_sha256, evaluation.tree_sha256)
            assert record["prepare_module_version"] == PREPARE_MODULE_VERSION


def test_a_line_break_in_a_name_makes_the_tree_hash_ambiguous(tmp_path: Path) -> None:
    """Why the loader refuses such names (Day 9 decision H1): two trees, one tree_sha256.

    Files x and y, and a single file named "x TAB <hash of x> LF y" holding y's bytes,
    give the same "path TAB file-SHA-256" lines. tree_sha256 itself is unchanged.
    """
    first, second, hidden = tmp_path / "first", tmp_path / "second", tmp_path / "hidden"
    for folder in (first, second, hidden):
        folder.mkdir()
    crafted = "x\t" + hashlib.sha256(b"1").hexdigest() + "\ny"
    try:
        (second / crafted).write_bytes(b"2")
    except OSError:
        pytest.skip("this file system cannot hold a line break in a name")
    (first / "x").write_bytes(b"1")
    (first / "y").write_bytes(b"2")
    for root in (first, second):
        (root / "m.py").write_bytes(b"# T\n")
    hashes = {tree_sha256(root, "m.py", "# T\n", hidden, "t") for root in (first, second)}
    assert len(hashes) == 1
