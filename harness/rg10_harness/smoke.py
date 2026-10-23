"""End-to-end smoke test of the harness with a real sandbox, on hand-written inputs only.

``python -m rg10_harness smoke --image IMAGE --workdir DIR`` writes a small materials and
generation set, assembles it into a bundle through DockerSandbox, evaluates the bundle in
TDEA through ResearchService, and checks every result against the expected table below.
Nothing here is model output or study data: every "output" is a fixed text written for
this test, and the probes are benign checks of the sandbox itself.

Expected results (TDEA's evaluation of each output: evaluability, insertable, import
status, hidden suite, project suite):

* g1-good: ok, True, ok, pass, pass. g2-wrong: ok, True, ok, fail, pass.
* g3-import-error: ok, False, error, fail, fail (a module that does not exist).
* g4-no-block: ok, False, none, fail, fail (no code block, D-17).
* g5-syntax: ok, False, not_attempted, fail, fail (does not parse; no record needed).
* g6-missing: missing output (listed, never dropped). The run ends with exclusions.
* g7-slow: ok, True, ok, fail, pass (both hidden tests hit the test time-out).
* Probes, each also a correct solution, so a passing probe is ok, True, ok, pass, pass;
  a probe that finds a breach raises "ISOLATION BREACH: ..." at import, which shows as an
  import error naming the breach:
  - p-network: a TCP connection to 1.1.1.1:443 and a DNS lookup must both fail;
  - p-filesystem: writing to /etc, to the read-only tree mount /work and to / must fail;
  - p-identity: the user is 65534, Python is 3.11, and the host's RG10_SMOKE_CANARY
    variable is absent;
  - p-host-path: the smoke work folder on the host must not exist inside the container
    (and the host checks afterwards that the probe left no file there);
  - p-memory: writing twice the memory limit must fail: ok, False, error, fail, fail.

The TDEA side needs the research environment (NFR-24: Python 3.11 in a virtual
environment, Pylint 4.1.1 and the pinned astroid and radon). It runs with its own key
file, lint cache, lock folder and research store inside the work folder, so it never
touches ~/.tdea. A JSON report (smoke-report.json) records the fingerprint, each output's
result and each check; it holds no study data.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rg10_harness.assemble import AssemblyReport, assemble
from rg10_harness.sandbox import Sandbox
from tdea.aggregator.research_service import ResearchService, ServicePaths
from tdea.analyzer.research.rg10.bundle import BundleLoader
from tdea.analyzer.research.rg10.execution import evaluate_bundle

CANARY = "RG10_SMOKE_CANARY"
MARKER = "# TASK: discounted"
MODULE = ('"""Orders of the fixture shop."""\n\n\n'
          "def total(prices):\n"
          '    """Return the sum of the prices."""\n'
          "    return sum(prices)\n\n\n"
          f"{MARKER}\n")
HIDDEN = ("test_orders.py", "from shop.orders import discounted\n\n\n"
          "def test_discounted():\n    assert discounted(100, 0.25) == 75\n\n\n"
          "def test_rate_zero():\n    assert discounted(5, 0) == 5\n")
PROJECT = ("tests/test_total.py", "from shop.orders import total\n\n\n"
           "def test_total():\n    assert total([1, 2]) == 3\n")
TASK = {"task_id": "task-1", "project": "shop", "target_module": "shop/orders.py",
        "marker": MARKER, "function_names": ["discounted"],
        "hidden_tests": [".hidden_tests/task-1/test_orders.py::test_discounted",
                         ".hidden_tests/task-1/test_orders.py::test_rate_zero"],
        "project_tests": ["tests/test_total.py::test_total"],
        "declared_opportunities": {"T1": 1, "T2": 1}}
FENCE = "```"
SOLUTION = "def discounted(price, rate):\n    return price * (1 - rate)\n"
OUTPUTS = {
    "g1-good": f"Here it is.\n{FENCE}python\n{SOLUTION}{FENCE}\n",
    "g2-wrong": f"{FENCE}python\ndef discounted(price, rate):\n    return price\n{FENCE}\n",
    "g3-import-error": f"{FENCE}python\nimport rg10_no_such_module\n\n\n"
                       f"def discounted(price, rate):\n    return price\n{FENCE}\n",
    "g4-no-block": "I cannot write that function.\n",
    "g5-syntax": f"{FENCE}python\ndef discounted(price, rate:\n    return price\n{FENCE}\n",
    "g7-slow": f"{FENCE}python\ndef discounted(price, rate):\n    while True:\n"
               f"        pass\n{FENCE}\n",
}
MISSING = "g6-missing"
PASSING = ("ok", True, "ok", "pass", "pass")
EXPECTED = {
    "g1-good": PASSING,
    "g2-wrong": ("ok", True, "ok", "fail", "pass"),
    "g3-import-error": ("ok", False, "error", "fail", "fail"),
    "g4-no-block": ("ok", False, None, "fail", "fail"),
    "g5-syntax": ("ok", False, "not_attempted", "fail", "fail"),
    MISSING: ("missing output", None, None, None, None),
    "g7-slow": ("ok", True, "ok", "fail", "pass"),
}
PROBE_EXPECTED = {"p-network": PASSING, "p-filesystem": PASSING, "p-identity": PASSING,
                  "p-host-path": PASSING, "p-memory": ("ok", False, "error", "fail", "fail")}
HOST_SENTINEL = "rg10-smoke-host-sentinel"


def probe_outputs(host_folder: Path, memory_limit_mb: int) -> dict[str, str]:
    """Return the probe outputs: import-time checks of the sandbox, then a correct solution."""
    sentinel = (host_folder / HOST_SENTINEL).as_posix()
    checks = {
        "p-network": (
            "import socket\n"
            "for _probe in (lambda: socket.create_connection(('1.1.1.1', 443), timeout=3),\n"
            "               lambda: socket.getaddrinfo('github.com', 443)):\n"
            "    try:\n        _probe()\n    except OSError:\n        continue\n"
            "    raise RuntimeError('ISOLATION BREACH: network reachable')\n"),
        "p-filesystem": (
            "for _path in ('/etc/rg10-probe', '/work/rg10-probe', '/rg10-probe'):\n"
            "    try:\n        open(_path, 'w', encoding='utf-8').close()\n"
            "    except OSError:\n        continue\n"
            "    raise RuntimeError('ISOLATION BREACH: wrote ' + _path)\n"),
        "p-identity": (
            "import os, sys\n"
            "if os.getuid() != 65534:\n"
            "    raise RuntimeError('ISOLATION BREACH: user ' + str(os.getuid()))\n"
            "if sys.version_info[:2] != (3, 11):\n"
            "    raise RuntimeError('ISOLATION BREACH: Python ' + sys.version)\n"
            f"if {CANARY!r} in os.environ:\n"
            "    raise RuntimeError('ISOLATION BREACH: host environment visible')\n"),
        "p-host-path": (
            "import os\n"
            f"if os.path.exists({host_folder.as_posix()!r}):\n"
            "    raise RuntimeError('ISOLATION BREACH: host folder visible')\n"
            f"try:\n    open({sentinel!r}, 'w', encoding='utf-8').close()\n"
            "except OSError:\n    pass\n"),
        "p-memory": f"_block = b'x' * ({2 * memory_limit_mb} * 1024 * 1024)\n",
    }
    return {name: f"{FENCE}python\n{code}\n\n{SOLUTION}{FENCE}\n"
            for name, code in checks.items()}


def write_inputs(root: Path, extra_outputs: dict[str, str] | None = None) -> tuple[Path, Path]:
    """Write the materials and generation folders under ``root``; return both."""
    outputs = {**OUTPUTS, **(extra_outputs or {})}
    materials, generation = root / "materials", root / "generation"
    for smell in ("T1", "T2"):
        for condition in ("clean", "placebo", "injected"):
            variant = materials / "variants" / smell / condition
            (variant / "shop").mkdir(parents=True)
            (variant / "shop" / "__init__.py").write_text("", encoding="utf-8")
            (variant / "shop" / "orders.py").write_text(MODULE, encoding="utf-8")
            (variant / "tests").mkdir()
            (variant / PROJECT[0]).write_text(PROJECT[1], encoding="utf-8")
    (materials / "tests" / "task-1").mkdir(parents=True)
    (materials / "tests" / "task-1" / HIDDEN[0]).write_text(HIDDEN[1], encoding="utf-8")
    (materials / "tasks.jsonl").write_text(json.dumps(TASK) + "\n", encoding="utf-8")
    gen_ids = sorted([*outputs, MISSING])
    rows = [{"gen_id": gen_id, "prompt_file": f"prompts/{gen_id}.txt",
             "output_file": f"{gen_id}.txt", "task_id": "task-1", "smell": "T1",
             "condition": "injected", "dose": None, "replicate": 1, "run_order": order}
            for order, gen_id in enumerate(gen_ids, 1)]
    (materials / "manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (generation / "outputs").mkdir(parents=True)
    for gen_id, text in outputs.items():
        (generation / "outputs" / f"{gen_id}.txt").write_text(text, encoding="utf-8")
    (generation / "generation.jsonl").write_text(
        "".join(json.dumps({"gen_id": gen_id, "model_id": "hand-written"}) + "\n"
                for gen_id in gen_ids), encoding="utf-8")
    return materials, generation


@dataclass
class SmokeResult:
    """The outcome of one smoke run: every check, and whether all passed."""

    checks: list[tuple[str, bool, str]]
    report: dict

    @property
    def passed(self) -> bool:
        """Return True when every check passed."""
        return all(ok for _name, ok, _detail in self.checks)


def run_smoke(sandbox: Sandbox, workdir: Path, *, probes: bool = True,
              memory_limit_mb: int = 1024,
              service: Callable[[Path], ResearchService] | None = None) -> SmokeResult:
    """Assemble, evaluate and check the smoke bundle in the new folder ``workdir``."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True)
    expected = dict(EXPECTED)
    extra: dict[str, str] = {}
    if probes:
        extra = probe_outputs(workdir, memory_limit_mb)
        expected.update(PROBE_EXPECTED)
    materials, generation = write_inputs(workdir / "inputs", extra)
    previous = os.environ.get(CANARY)
    os.environ[CANARY] = "host-only"  # the sandbox must not pass it on
    try:
        assembled = assemble(materials, generation, workdir / "bundle", sandbox, "rg10-smoke")
    finally:
        if previous is None:
            os.environ.pop(CANARY, None)
        else:
            os.environ[CANARY] = previous
    found = evaluation_table(workdir / "bundle")
    checks = [(f"output {gen_id}", found.get(gen_id) == want,
               f"expected {list(want)}, got {list(found.get(gen_id) or ())}")
              for gen_id, want in sorted(expected.items())]
    checks.append(("every output with a module got a sandbox reply", not assembled.no_reply,
                   f"no reply: {assembled.no_reply}"))
    if probes:
        checks.append(("probe left no file in the host folder",
                       not (workdir / HOST_SENTINEL).exists(), str(workdir / HOST_SENTINEL)))
    checks.append(_tdea_run(workdir, service, len(expected)))
    fingerprint = json.loads((workdir / "bundle" / "bundle.json").read_text(
        encoding="utf-8"))["env_fingerprint"]
    report = {"env_fingerprint": fingerprint, "assembly": _assembly(assembled),
              "outputs": {gen_id: list(row) for gen_id, row in sorted(found.items())},
              "import_errors": _import_errors(workdir / "bundle"),
              "checks": [{"check": name, "passed": ok, "detail": detail}
                         for name, ok, detail in checks]}
    (workdir / "smoke-report.json").write_text(json.dumps(report, indent=2) + "\n",
                                               encoding="utf-8")
    return SmokeResult(checks, report)


def evaluation_table(bundle: Path) -> dict[str, tuple]:
    """Return TDEA's evaluation of each output, as in the expected table."""
    return {item.gen_id: (item.match.evaluability, item.match.insertable,
                          item.match.import_status, item.match.hidden_suite_result,
                          item.match.project_suite_result)
            for item in evaluate_bundle(BundleLoader().load(bundle))}


def _tdea_run(workdir: Path, service: Callable[[Path], ResearchService] | None,
              outputs: int) -> tuple[str, bool, str]:
    """Evaluate the bundle through ResearchService into a store in the work folder."""
    run_file = workdir / "rg10.yaml"
    run_file.write_text("kind: rg10\nstore: research.db\nbundle: bundle\n", encoding="utf-8")
    paths = ServicePaths(lock_directory=workdir / "lock",
                         lint_cache=workdir / "cache" / "lintcache.db",
                         key_file=workdir / "keys" / "research.key")
    research = service(workdir) if service is not None else ResearchService(paths=paths)
    result = research.run(run_file)
    ok = (result.outputs, result.unevaluable, result.with_exclusions) == (outputs, 1, True)
    return ("TDEA run: every output stored, the missing one excluded (exit 2)", ok,
            f"outputs {result.outputs}, unevaluable {result.unevaluable}")


def _assembly(report: AssemblyReport) -> dict[str, list[str]]:
    """Return the assembly report as lists of gen_ids."""
    return {"recorded": report.recorded, "missing_output": report.missing_output,
            "no_module": report.no_module, "not_parsing": report.not_parsing,
            "no_reply": report.no_reply}


def _import_errors(bundle: Path) -> dict[str, str]:
    """Return the import error of each record that has one (shows any probe breach)."""
    errors = {}
    for line in (bundle / "execution.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("import_error"):
            errors[record["gen_id"]] = record["import_error"]
    return errors


def remove(workdir: Path) -> None:
    """Delete a smoke work folder."""
    shutil.rmtree(workdir, ignore_errors=True)
