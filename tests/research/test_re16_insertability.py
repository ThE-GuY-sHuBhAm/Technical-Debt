"""RE-16 (FR-59, NFR-26), component level: insertability from execution records.

RE-16: "Syntax and import errors non-insertable; the other three insertable; import json
flagged; redefinition flagged; both tampered outputs unevaluable (SHA mismatch), listed,
run exits 2; TDEA imports nothing". FX-RG10's seven RE-16 outputs are evaluated with
evaluate_bundle; "listed" is checked as the list of unevaluable outputs with their
reasons. "Run exits 2" needs the RG10 run (Day 9 decisions D9-1 and D9-4, Option B) and
is not checked here, so RE-16 is not passed. "TDEA imports nothing" is checked by
watching every import, compile, exec and eval call during loading and evaluation, by
the bundle's own module names staying out of sys.modules, and by the sentinel output's
file never appearing. The new-import and redefinition definitions are D9-5's
provisional ones. Day 9 decision D8 is checked at the end: a module that does not parse
has import status not_attempted, an output without a module has none, and a record
written for either is not read beyond its gen_id and fingerprint. FX-RG10 is SYNTHETIC.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import shutil
import sys
from pathlib import Path

import make_fx_rg10
import pytest
from make_fx_rg10 import GEN_IDS, SENTINEL_VARIABLE

from tdea.analyzer.research.rg10.bundle import BundleInvalid, BundleLoader
from tdea.analyzer.research.rg10.execution import (IMPORT_NOT_ATTEMPTED, OK, SHA_MISMATCH,
                                                   OutputEvaluation, evaluate_bundle,
                                                   failed_by_rule)

# label -> (parses, insertable, import status, evaluability, new imports, redefinitions)
EXPECTED = {
    "syntax_error": (False, False, IMPORT_NOT_ATTEMPTED, OK, None, None),
    "import_missing": (True, False, "error", OK, ("fx_rg10_absent_dependency",), ()),
    "new_import": (True, True, "ok", OK, ("json",), ()),
    "redefinition": (True, True, "ok", OK, (), ("total",)),
    "clean": (True, True, "ok", OK, (), ()),
    "tampered_module": (True, None, None, SHA_MISMATCH, (), ()),
    "tampered_tree": (True, None, None, SHA_MISMATCH, (), ()),
    "sentinel": (True, True, "ok", OK, ("os", "pathlib"), ()),
}


@pytest.fixture(name="evaluated", scope="module")
def _evaluated(fx_rg10: make_fx_rg10.FxRg10) -> dict[str, OutputEvaluation]:
    """Return FX-RG10's evaluations by label."""
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(fx_rg10.path))}
    return {label: found[gen_id] for label, gen_id in GEN_IDS.items()}


@pytest.mark.parametrize("label", list(EXPECTED))
def test_insertability_and_static_flags(evaluated: dict[str, OutputEvaluation],
                                        label: str) -> None:
    """Each RE-16 output gets its insertability, evaluability and flags."""
    evaluation = evaluated[label]
    assert (evaluation.parses, evaluation.match.insertable, evaluation.match.import_status,
            evaluation.match.evaluability, evaluation.new_imports,
            evaluation.redefinitions) == EXPECTED[label]


def test_non_insertable_outputs_fail_both_suites_by_rule(
        evaluated: dict[str, OutputEvaluation]) -> None:
    """The syntax error and the import error fail both suites with source "rule"."""
    for label in ("syntax_error", "import_missing"):
        match = evaluated[label].match
        assert (match.hidden_suite_result, match.project_suite_result,
                match.suite_result_source, match.tests) == ("fail", "fail", "rule", ())


def test_tampered_outputs_are_the_listed_exclusions(
        evaluated: dict[str, OutputEvaluation]) -> None:
    """Exactly the two tampered outputs are unevaluable, each with "SHA mismatch"."""
    excluded = sorted((evaluation.gen_id, evaluation.match.evaluability)
                      for evaluation in evaluated.values() if evaluation.match.evaluability != OK)
    assert excluded == [(GEN_IDS["tampered_module"], SHA_MISMATCH),
                        (GEN_IDS["tampered_tree"], SHA_MISMATCH)]
    for label in ("tampered_module", "tampered_tree"):
        match = evaluated[label].match
        assert (match.hidden_suite_result, match.suite_result_source, match.tests) == (
            None, None, ())


def test_tampering_one_hash_is_enough(fx_rg10: make_fx_rg10.FxRg10,
                                      evaluated: dict[str, OutputEvaluation]) -> None:
    """Each tampered record differs from TDEA's rebuild in exactly the hash it tampered."""
    records = {record["gen_id"]: record
               for record in make_fx_rg10.read_jsonl(fx_rg10.path / "execution.jsonl")}
    for label, tampered, kept in (("tampered_module", "module_sha256", "tree_sha256"),
                                  ("tampered_tree", "tree_sha256", "module_sha256")):
        evaluation = evaluated[label]
        record = records[evaluation.gen_id]
        assert record[tampered] != getattr(evaluation, tampered)
        assert record[kept] == getattr(evaluation, kept)


def test_the_syntax_error_needs_no_record(fx_rg10: make_fx_rg10.FxRg10,
                                          evaluated: dict[str, OutputEvaluation]) -> None:
    """A module that does not parse has no record and is still evaluable (FR-59)."""
    loaded = BundleLoader().load(fx_rg10.path)
    assert GEN_IDS["syntax_error"] not in loaded.records
    assert evaluated["syntax_error"].parse_error == "SyntaxError: expected ':' (line 18)"


def test_the_deeply_nested_module_does_not_parse(evaluated: dict[str, OutputEvaluation]) -> None:
    """A module nested 10,000 parentheses deep is recorded as not parsing (D9-6)."""
    evaluation = evaluated["nested"]
    assert evaluation.parses is False and evaluation.match.insertable is False
    assert evaluation.parse_error == "SyntaxError: too many nested parentheses (line 10)"


def test_tdea_imports_runs_and_compiles_nothing(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """No bundle module is imported, nothing is exec'd or eval'd, and compile builds ASTs only.

    The sentinel output would write a file at import time; it never appears.
    """
    sentinel = tmp_path / "sentinel.txt"
    monkeypatch.setenv(SENTINEL_VARIABLE, str(sentinel))
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    real_import, real_compile = builtins.__import__, builtins.compile
    real_import_module = importlib.import_module

    def watched_import(name: str, *args: object, **kwargs: object) -> object:
        calls.append(f"import {name}")
        return real_import(name, *args, **kwargs)

    def watched_import_module(name: str, *args: object, **kwargs: object) -> object:
        calls.append(f"import_module {name}")
        return real_import_module(name, *args, **kwargs)

    def watched_compile(source: object, filename: object, mode: str, *args: object,
                        **kwargs: object) -> object:
        flags = int(str(args[0] if args else kwargs.get("flags", 0)))
        calls.append("compile" if flags & ast.PyCF_ONLY_AST else "compile to code")
        return real_compile(source, filename, mode, *args, **kwargs)

    def refused(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("exec or eval was called")

    loader = BundleLoader()
    monkeypatch.setattr(builtins, "__import__", watched_import)
    monkeypatch.setattr(importlib, "import_module", watched_import_module)
    monkeypatch.setattr(builtins, "compile", watched_compile)
    monkeypatch.setattr(builtins, "exec", refused)
    monkeypatch.setattr(builtins, "eval", refused)
    calls.clear()  # monkeypatch's own lazy imports are not TDEA's
    evaluations = evaluate_bundle(loader.load(fx_rg10.path))
    watched = list(calls)
    monkeypatch.undo()
    calls[:] = watched
    assert len(evaluations) == len(GEN_IDS)
    assert "compile to code" not in calls and "compile" in calls
    # The only import TDEA's own code makes here is ast.walk's "from collections import
    # deque"; the bundle's own imports (os, pathlib, json, shop, ...) would show up here.
    assert {call for call in calls if call.startswith("import")} == {"import collections"}
    assert "shop" not in sys.modules and "shop.orders" not in sys.modules
    assert "fx_rg10_absent_dependency" not in sys.modules
    assert not sentinel.exists() and not (tmp_path / "fx_rg10_sentinel.txt").exists()


# --- Day 9 decision D8: outputs that need no record -------------------------------------

def test_import_status_without_a_module_and_for_a_parse_failure(
        evaluated: dict[str, OutputEvaluation]) -> None:
    """D8: no module (none, unterminated) gives None; a parse failure gives not_attempted.

    Both fail their suites by rule and stay evaluable.
    """
    for label in ("no_block", "unterminated", "only_js"):
        assert evaluated[label].module_sha256 is None
        assert evaluated[label].match == failed_by_rule()
    for label in ("syntax_error", "nested"):
        assert evaluated[label].module_sha256 is not None
        assert evaluated[label].match == failed_by_rule(IMPORT_NOT_ATTEMPTED)


def with_extra_record(fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path, label: str,
                      **fields: object) -> Path:
    """Return a copy of FX-RG10 with one more record, for ``label``'s output."""
    bundle = shutil.copytree(fx_rg10.path, tmp_path / "copy")
    records = make_fx_rg10.read_jsonl(bundle / "execution.jsonl")
    records.append(dict(records[0], gen_id=GEN_IDS[label], module_sha256="0" * 64,
                        tree_sha256="1" * 64, **fields))
    make_fx_rg10.write_jsonl(bundle / "execution.jsonl", records)
    return bundle


@pytest.mark.parametrize("label, fields, expected", [
    pytest.param("syntax_error", {"import_status": "ok"}, IMPORT_NOT_ATTEMPTED,
                 id="parse failure, record claims ok"),
    pytest.param("syntax_error", {"import_status": "not_attempted", "tests": None},
                 IMPORT_NOT_ATTEMPTED, id="parse failure, record not_attempted"),
    pytest.param("syntax_error", {"tdea_version": 1, "executed_at": None},
                 IMPORT_NOT_ATTEMPTED, id="parse failure, record malformed"),
    pytest.param("no_block", {"import_status": "ok"}, None, id="no module, record claims ok"),
])
def test_a_record_for_an_output_that_needs_none_is_not_read(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path, label: str, fields: dict[str, object],
        expected: str | None) -> None:
    """D8: wrong hashes, a claimed import or malformed fields change nothing."""
    bundle = with_extra_record(fx_rg10, tmp_path, label, **fields)
    found = {evaluation.gen_id: evaluation
             for evaluation in evaluate_bundle(BundleLoader().load(bundle))}
    assert found[GEN_IDS[label]].match == failed_by_rule(expected)


def test_a_record_for_an_output_that_needs_none_still_needs_the_fingerprint(
        fx_rg10: make_fx_rg10.FxRg10, tmp_path: Path) -> None:
    """D8: gen_id and env_fingerprint are checked for every record, so this one refuses."""
    fingerprint = dict(make_fx_rg10.ENV_FINGERPRINT, memory_limit_mb=2048)
    bundle = with_extra_record(fx_rg10, tmp_path, "syntax_error", env_fingerprint=fingerprint)
    with pytest.raises(BundleInvalid) as caught:
        BundleLoader().load(bundle)
    assert (caught.value.file, caught.value.field) == ("execution.jsonl", "env_fingerprint")
