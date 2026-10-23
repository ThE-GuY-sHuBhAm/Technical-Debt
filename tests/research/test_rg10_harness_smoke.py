"""The smoke test's own logic, run with the local sandbox double instead of Docker.

This shows that the smoke command's expected table, its TDEA run and its report agree with
the harness and TDEA on the hand-written outputs. It does not run Docker and leaves the
isolation probes out: they only mean something inside the real container, which the CI
job "RG10 harness smoke test (real Docker)" and the guide in
docs/research/rg10-harness-smoke-test.md cover. Python 3.11 only, like tests/research.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rg10_harness_fixture import LocalSandbox
from run_support import PINNED_FACTS

from rg10_harness.sandbox import Limits
from rg10_harness.smoke import EXPECTED, PROBE_EXPECTED, probe_outputs, run_smoke
from tdea.aggregator.research_service import ResearchService, ServicePaths


def service(workdir: Path) -> ResearchService:
    """Return a research service writing only inside ``workdir``, with pinned facts."""
    return ResearchService(paths=ServicePaths(
        lock_directory=workdir / "lock", lint_cache=workdir / "cache" / "lintcache.db",
        key_file=workdir / "keys" / "research.key"), environment=PINNED_FACTS)


def test_every_check_passes_without_probes(tmp_path: Path) -> None:
    """Expected table, sandbox replies and the TDEA run all pass; the report says so."""
    result = run_smoke(LocalSandbox(Limits(import_timeout_s=5, test_timeout_s=3)),
                       tmp_path / "smoke", probes=False, service=service)
    assert [name for name, ok, _detail in result.checks if not ok] == []
    assert len(result.checks) == len(EXPECTED) + 2
    report = json.loads((tmp_path / "smoke" / "smoke-report.json").read_text(encoding="utf-8"))
    assert all(check["passed"] for check in report["checks"])
    assert report["import_errors"] == {
        "g3-import-error": "ModuleNotFoundError: No module named 'rg10_no_such_module'"}
    assert not (Path.home() / ".tdea" / "smoke").exists()


def test_a_wrong_result_fails_its_check(tmp_path: Path) -> None:
    """A sandbox that loses one reply makes the smoke test fail, naming the output."""

    class Losing(LocalSandbox):
        """Drops the reply for the good output."""

        def execute(self, job):
            """Return None for the module holding the correct solution only."""
            text = (job.tree / job.target_module).read_text(encoding="utf-8")
            return None if "price * (1 - rate)" in text else super().execute(job)

    result = run_smoke(Losing(Limits(import_timeout_s=5, test_timeout_s=3)),
                       tmp_path / "smoke", probes=False, service=service)
    failed = [name for name, ok, _detail in result.checks if not ok]
    assert "output g1-good" in failed and not result.passed


def test_an_existing_folder_is_refused(tmp_path: Path) -> None:
    """The smoke test only writes into a new folder."""
    with pytest.raises(FileExistsError):
        run_smoke(LocalSandbox(Limits()), tmp_path, probes=False, service=service)


def test_probes_name_the_host_folder_and_the_memory_limit(tmp_path: Path) -> None:
    """Each probe is a correct solution after its check; the checks carry the host values."""
    probes = probe_outputs(tmp_path, 512)
    assert sorted(probes) == sorted(PROBE_EXPECTED)
    assert tmp_path.as_posix() in probes["p-host-path"]
    assert "_block = b'x' * (1024 * 1024 * 1024)" in probes["p-memory"]  # twice 512 MB
    assert all("def discounted(price, rate):" in text for text in probes.values())
