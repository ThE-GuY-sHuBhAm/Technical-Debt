"""Harness to TDEA, end to end: a bundle the harness assembled evaluates as intended.

The hand-written inputs (rg10_harness_fixture) are prepared, run through runner.py in a
child process (LocalSandbox standing in for the Docker sandbox) and assembled; TDEA then
loads and evaluates the bundle as text (FR-58 to FR-60), never importing a module. The
module and tree hashes the harness recorded must equal the ones TDEA recomputes, so every
output with a record is evaluable. Python 3.11 only, like the rest of tests/research.
"""

from __future__ import annotations

from pathlib import Path

from rg10_harness_fixture import LocalSandbox, write_inputs

from rg10_harness.assemble import assemble
from rg10_harness.sandbox import Limits
from tdea.analyzer.research.rg10.bundle import BundleLoader
from tdea.analyzer.research.rg10.execution import evaluate_bundle


def test_tdea_evaluates_the_harness_bundle(tmp_path: Path) -> None:
    """Insertable, failing, import error, no block, no parse, missing and time-out cases."""
    materials, generation = write_inputs(tmp_path / "in")
    sandbox = LocalSandbox(Limits(import_timeout_s=5, test_timeout_s=3))
    report = assemble(materials, generation, tmp_path / "bundle", sandbox, "e2e-01")
    assert not report.no_reply and report.not_parsing == ["g5-syntax"]
    evaluations = {item.gen_id: item for item in evaluate_bundle(
        BundleLoader().load(tmp_path / "bundle"))}
    summary = {gen_id: (item.match.evaluability, item.match.insertable,
                        item.match.import_status, item.match.hidden_suite_result,
                        item.match.project_suite_result)
               for gen_id, item in evaluations.items()}
    assert summary == {
        "g1-good": ("ok", True, "ok", "pass", "pass"),
        "g2-wrong": ("ok", True, "ok", "fail", "pass"),
        "g3-import-error": ("ok", False, "error", "fail", "fail"),
        "g4-no-block": ("ok", False, None, "fail", "fail"),
        "g5-syntax": ("ok", False, "not_attempted", "fail", "fail"),
        "g6-missing": ("missing output", None, None, None, None),
        "g7-slow": ("ok", True, "ok", "fail", "pass"),
    }
    slow = {outcome.test_id: outcome.outcome for outcome in evaluations["g7-slow"].match.tests}
    assert set(slow.values()) == {"timeout", "pass"}
    assert all(item.match.tests_evaluable in (True, None) for item in evaluations.values())
