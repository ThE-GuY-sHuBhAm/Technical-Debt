"""The hand-written RG10 harness inputs (from rg10_harness.smoke) and a local sandbox double.

The inputs are written by hand: the "model outputs" are fixed texts, not generated
code, and every module is benign (a sum, a discount, an import of a module
that does not exist, a loop that never ends inside a test). The harness tests run it
through runner.py in a child process on the test machine (LocalSandbox), which stands in
for the Docker sandbox; no model output from any study is ever run this way.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
import tempfile

from rg10_harness.sandbox import RUNNER, Job, Limits, job_line, parse_reply
from rg10_harness.smoke import MISSING, OUTPUTS, TASK, write_inputs

__all__ = ("LocalSandbox", "MISSING", "OUTPUTS", "TASK", "write_inputs")


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
