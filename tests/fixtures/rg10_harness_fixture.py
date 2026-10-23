"""A small hand-written materials and generation set for the RG10 harness tests.

Everything here is written by hand for the tests: the "model outputs" are fixed texts,
not generated code, and every module is benign (a sum, a discount, an import of a module
that does not exist, a loop that never ends inside a test). The harness tests run it
through runner.py in a child process on the test machine (LocalSandbox), which stands in
for the Docker sandbox; no model output from any study is ever run this way.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from rg10_harness.sandbox import RUNNER, Job, Limits, job_line, parse_reply

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
OUTPUTS = {
    "g1-good": f"Here it is.\n{FENCE}python\ndef discounted(price, rate):\n"
               f"    return price * (1 - rate)\n{FENCE}\n",
    "g2-wrong": f"{FENCE}python\ndef discounted(price, rate):\n    return price\n{FENCE}\n",
    "g3-import-error": f"{FENCE}python\nimport rg10_no_such_module\n\n\n"
                       f"def discounted(price, rate):\n    return price\n{FENCE}\n",
    "g4-no-block": "I cannot write that function.\n",
    "g5-syntax": f"{FENCE}python\ndef discounted(price, rate:\n    return price\n{FENCE}\n",
    "g7-slow": f"{FENCE}python\ndef discounted(price, rate):\n    while True:\n"
               f"        pass\n{FENCE}\n",
}
MISSING = "g6-missing"


def write_inputs(root: Path) -> tuple[Path, Path]:
    """Write the materials and generation folders under ``root``; return both."""
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
    gen_ids = [*OUTPUTS, MISSING]
    rows = [{"gen_id": gen_id, "prompt_file": f"prompts/{gen_id}.txt",
             "output_file": f"{gen_id}.txt", "task_id": "task-1", "smell": "T1",
             "condition": "injected", "dose": None, "replicate": 1, "run_order": order}
            for order, gen_id in enumerate(sorted(gen_ids), 1)]
    (materials / "manifest.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (generation / "outputs").mkdir(parents=True)
    for gen_id, text in OUTPUTS.items():
        (generation / "outputs" / f"{gen_id}.txt").write_text(text, encoding="utf-8")
    (generation / "generation.jsonl").write_text(
        "".join(json.dumps({"gen_id": gen_id, "model_id": "hand-written"}) + "\n"
                for gen_id in sorted(gen_ids)), encoding="utf-8")
    return materials, generation


class LocalSandbox:
    """Test double for DockerSandbox: runs runner.py in a child process, no container."""

    def __init__(self, limits: Limits) -> None:
        """Use ``limits`` for the time-outs, as the Docker sandbox would."""
        self.limits = limits
        self.jobs: list[Job] = []

    def fingerprint(self) -> dict:
        """Return a fingerprint shaped like DockerSandbox's, for this interpreter."""
        return {"image_digest": "local-test-double", "python_version": sys.version.split()[0],
                "pytest_version": "test", "import_timeout_s": self.limits.import_timeout_s,
                "test_timeout_s": self.limits.test_timeout_s,
                "cpu_limit": self.limits.cpu_limit,
                "memory_limit_mb": self.limits.memory_limit_mb, "network": "none"}

    def execute(self, job: Job) -> dict | None:
        """Run the runner on ``job`` and return its result."""
        self.jobs.append(job)
        nonce = secrets.token_hex(16)
        with tempfile.TemporaryDirectory(prefix="rg10-scratch-") as scratch:
            done = subprocess.run([sys.executable, "-I", str(RUNNER), str(job.tree), scratch],
                                  input=job_line(job, self.limits, nonce),
                                  capture_output=True, text=True, check=False, timeout=300)
        return parse_reply(done.stdout, nonce)
