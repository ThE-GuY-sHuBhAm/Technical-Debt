"""Bundle assembly (RG10 pipeline steps 3 and 5) with a sandbox that executes nothing.

CannedSandbox answers every job with a fixed result, so these tests check preparation,
the execution-record fields, bundle.json and the refusals without running any module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rg10_harness_fixture import MISSING, OUTPUTS, TASK, write_inputs

from rg10_harness import HARNESS_VERSION
from rg10_harness.assemble import AssemblyError, assemble, read_jsonl
from rg10_harness.sandbox import Job, SandboxRefused
from tdea import __version__ as TDEA_VERSION
from tdea.analyzer.research.rg10.bundle import BundleLoader
from tdea.analyzer.research.rg10.prepare import PREPARE_MODULE_VERSION, module_sha256

FINGERPRINT = {"image_digest": "sha256:" + "ef" * 32, "python_version": "3.11.11",
               "pytest_version": "8.3.3", "import_timeout_s": 10, "test_timeout_s": 30,
               "cpu_limit": 1, "memory_limit_mb": 1024, "network": "none"}
OK = {"python_version": "3.11.11", "parses": True, "import_status": "ok",
      "import_error": None, "tests": [
          {"suite": "hidden", "test_id": test_id, "outcome": "pass", "duration_s": 0.1}
          for test_id in TASK["hidden_tests"]] + [
          {"suite": "project", "test_id": test_id, "outcome": "pass", "duration_s": 0.1}
          for test_id in TASK["project_tests"]]}
NOT_PARSING = {"python_version": "3.11.11", "parses": False, "import_status": "not_attempted",
               "import_error": None, "tests": []}


class CannedSandbox:
    """Answers each job without executing it; a job whose module holds "while" gets None."""

    def __init__(self, refuse: bool = False) -> None:
        """Refuse at the fingerprint when ``refuse`` is set."""
        self.refuse = refuse
        self.jobs: list[Job] = []

    def fingerprint(self) -> dict:
        """Return the fixed fingerprint, or refuse."""
        if self.refuse:
            raise SandboxRefused("no image")
        return dict(FINGERPRINT)

    def execute(self, job: Job) -> dict | None:
        """Return a canned result chosen from the module text, which is only read."""
        self.jobs.append(job)
        text = (job.tree / job.target_module).read_text(encoding="utf-8")
        if "while True" in text:
            return None
        return NOT_PARSING if "rate:\n" in text else OK


def build(tmp_path: Path, sandbox: CannedSandbox | None = None):
    """Assemble the hand-written inputs into tmp_path/bundle and return the report."""
    materials, generation = write_inputs(tmp_path / "in")
    return assemble(materials, generation, tmp_path / "bundle", sandbox or CannedSandbox(),
                    "harness-test-01", clock=lambda: "2026-10-23T20:00:00Z")


def test_every_row_is_accounted_for(tmp_path: Path) -> None:
    """Recorded, missing output, no module, not parsing and no reply, by gen_id."""
    report = build(tmp_path)
    assert report.recorded == ["g1-good", "g2-wrong", "g3-import-error"]
    assert report.missing_output == [MISSING] and report.no_module == ["g4-no-block"]
    assert report.not_parsing == ["g5-syntax"] and report.no_reply == ["g7-slow"]
    assert len(report.recorded) + 4 == len(OUTPUTS) + 1


def test_the_bundle_has_the_frozen_layout_and_loads(tmp_path: Path) -> None:
    """bundle.json, the copied inputs and execution.jsonl; TDEA's loader accepts it."""
    build(tmp_path)
    bundle = tmp_path / "bundle"
    assert sorted(path.name for path in bundle.iterdir()) == [
        "bundle.json", "execution.jsonl", "generation.jsonl", "manifest.jsonl", "outputs",
        "tasks.jsonl", "tests", "variants"]
    assert json.loads((bundle / "bundle.json").read_text(encoding="utf-8")) == {
        "schema_version": "1", "bundle_id": "harness-test-01",
        "harness_version": HARNESS_VERSION, "prepare_module_version": PREPARE_MODULE_VERSION,
        "env_fingerprint": FINGERPRINT, "created_at": "2026-10-23T20:00:00Z"}
    loaded = BundleLoader().load(bundle)
    assert loaded.missing_outputs == (MISSING,)
    assert sorted(loaded.records) == ["g1-good", "g2-wrong", "g3-import-error"]
    assert not (tmp_path / "bundle.partial").exists()


def test_a_record_has_every_field_of_the_frozen_table(tmp_path: Path) -> None:
    """Each record carries the module and tree hashes, versions, outcomes and fingerprint."""
    build(tmp_path)
    (record, *_rest) = read_jsonl(tmp_path / "bundle" / "execution.jsonl")
    assert sorted(record) == sorted([
        "gen_id", "module_sha256", "tree_sha256", "python_version", "prepare_module_version",
        "import_status", "import_error", "tests", "env_fingerprint", "executed_at",
        "harness_version", "tdea_version"])
    assert record["gen_id"] == "g1-good" and record["env_fingerprint"] == FINGERPRINT
    assert (record["prepare_module_version"], record["tdea_version"]) == (
        PREPARE_MODULE_VERSION, TDEA_VERSION)
    assert record["tests"] == OK["tests"] and len(record["tree_sha256"]) == 64


def test_the_executed_tree_is_the_hashed_one(tmp_path: Path) -> None:
    """The module the sandbox got is the one whose SHA-256 is recorded, with hidden tests."""
    seen: dict[str, str] = {}

    class Watching(CannedSandbox):
        """Records the module of each job and checks its tree and tests."""

        def execute(self, job: Job) -> dict | None:
            """Check the job, then answer as CannedSandbox does."""
            module = (job.tree / job.target_module).read_bytes().decode("utf-8")
            seen[module_sha256(module)] = module
            assert (job.tree / ".hidden_tests" / "task-1" / "test_orders.py").is_file()
            assert job.tests == (("hidden", TASK["hidden_tests"][0]),
                                 ("hidden", TASK["hidden_tests"][1]),
                                 ("project", TASK["project_tests"][0]))
            return super().execute(job)

    build(tmp_path, Watching())
    records = read_jsonl(tmp_path / "bundle" / "execution.jsonl")
    assert all(record["module_sha256"] in seen for record in records)


def test_an_existing_destination_is_refused(tmp_path: Path) -> None:
    """A bundle folder is never overwritten."""
    (tmp_path / "bundle").mkdir()
    with pytest.raises(AssemblyError, match="already exists"):
        build(tmp_path)


def test_a_refused_sandbox_leaves_nothing(tmp_path: Path) -> None:
    """The fingerprint is taken first; a refusal writes no folder."""
    with pytest.raises(SandboxRefused):
        build(tmp_path, CannedSandbox(refuse=True))
    assert not (tmp_path / "bundle").exists() and not (tmp_path / "bundle.partial").exists()


def test_missing_inputs_leave_nothing(tmp_path: Path) -> None:
    """A missing generation.jsonl stops assembly and removes the partial folder."""
    materials, generation = write_inputs(tmp_path / "in")
    (generation / "generation.jsonl").unlink()
    with pytest.raises(AssemblyError, match="generation.jsonl is missing"):
        assemble(materials, generation, tmp_path / "bundle", CannedSandbox(), "b")
    assert not (tmp_path / "bundle").exists() and not (tmp_path / "bundle.partial").exists()
