"""The sandbox: where the harness runs model code (RG10 pipeline step 4; Freeze Decision 1).

DockerSandbox runs runner.py in a fresh container per output, from one pinned image, and
reads back the result line. Every container gets the same limits, which the environment
fingerprint records once for the whole bundle (Freeze section 9: image digest, Python and
pytest versions, import and test time-outs, CPU and memory limits, network "none"):

* ``--network none``; a read-only root file system; a size-limited /tmp (tmpfs, noexec)
  as the only writable place; the executed tree and the runner mounted read-only;
* ``--cpus``, ``--memory`` with swap equal to memory, and ``--pids-limit``;
* all capabilities dropped, no-new-privileges, and the unprivileged user 65534;
* no environment variable from the host: the container sees no token, no TDEA key file,
  research store or lint cache, and no host folder but the two read-only mounts.

The host never imports or runs model code; it only builds the command, sends the job and
parses the reply. A container that does not finish within the job's overall time limit is
killed and the output gets no record (TDEA then lists it as unevaluable, "missing
execution"); a reply without the job's nonce is treated the same way.

The fingerprint's key names (image_digest, python_version, pytest_version,
import_timeout_s, test_timeout_s, cpu_limit, memory_limit_mb, network) are the ones the
FX-RG10 fixtures use; only "network" is read by TDEA. A sandbox whose interpreter is not
Python 3.11 is refused before anything runs (Freeze Decision 1, condition 2).
"""

from __future__ import annotations

import json
import secrets
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

RUNNER = Path(__file__).with_name("runner.py")
CONTAINER_TREE = "/work"
CONTAINER_RUNNER = "/harness/runner.py"
CONTAINER_SCRATCH = "/tmp"
NOBODY = "65534:65534"
FINGERPRINT_SNIPPET = ("import json, platform, importlib.metadata as m; print(json.dumps("
                       "{'python_version': platform.python_version(), "
                       "'pytest_version': m.version('pytest')}))")

Execute = Callable[..., subprocess.CompletedProcess]


class SandboxRefused(Exception):
    """The sandbox cannot be used for this bundle; nothing was executed."""


@dataclass(frozen=True)
class Limits:
    """The per-output limits, the same for every output of a bundle."""

    import_timeout_s: int = 10
    test_timeout_s: int = 30
    cpu_limit: float = 1
    memory_limit_mb: int = 1024
    scratch_mb: int = 256
    pids_limit: int = 256
    overhead_s: int = 60  # container start and copy, on top of the job's own time-outs


@dataclass(frozen=True)
class Job:
    """One parsing candidate module to execute: its tree, module and declared tests."""

    tree: Path
    target_module: str
    tests: tuple[tuple[str, str], ...]  # (suite, test_id), hidden tests first


class Sandbox(Protocol):
    """What the assembler needs from a sandbox."""

    def fingerprint(self) -> dict:
        """Return the environment fingerprint shared by every record of the bundle."""

    def execute(self, job: Job) -> dict | None:
        """Return the runner's result for ``job``, or None when there is no valid reply."""


class DockerSandbox:
    """Runs each job in a new container of ``image`` through the docker command line."""

    def __init__(self, image: str, limits: Limits = Limits(), docker: str = "docker",
                 run: Execute = subprocess.run) -> None:
        """Use ``image`` with ``limits``; ``run`` is replaceable for tests."""
        self.image = image
        self.limits = limits
        self._docker = docker
        self._run = run

    def container_command(self, name: str, mounts: Sequence[tuple[Path, str]],
                          command: Sequence[str]) -> list[str]:
        """Return the docker run command line for one container."""
        limits = self.limits
        line = [self._docker, "run", "--rm", "-i", "--name", name,
                "--network", "none", "--read-only",
                "--tmpfs", f"{CONTAINER_SCRATCH}:rw,noexec,nosuid,size={limits.scratch_mb}m",
                "--cpus", f"{limits.cpu_limit:g}", "--memory", f"{limits.memory_limit_mb}m",
                "--memory-swap", f"{limits.memory_limit_mb}m",
                "--pids-limit", str(limits.pids_limit),
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", NOBODY, "--env", f"HOME={CONTAINER_SCRATCH}",
                "--workdir", CONTAINER_SCRATCH]
        for source, target in mounts:
            line += ["--mount", f"type=bind,source={source},target={target},readonly"]
        return line + [self.image, *command]

    def fingerprint(self) -> dict:
        """Read the image digest and the interpreter and pytest versions; refuse non-3.11."""
        inspected = self._run([self._docker, "image", "inspect", "--format", "{{.Id}}",
                               self.image], capture_output=True, text=True, check=False)
        digest = inspected.stdout.strip()
        if inspected.returncode != 0 or not digest.startswith("sha256:"):
            raise SandboxRefused(f"The image {self.image} is not available locally; build "
                                 "or pull it first.")
        name = f"rg10-fingerprint-{secrets.token_hex(6)}"
        versions = self._run(self.container_command(name, (), ["python", "-I", "-c",
                                                               FINGERPRINT_SNIPPET]),
                             capture_output=True, text=True, check=False, timeout=120)
        try:
            found = json.loads(versions.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise SandboxRefused(f"The image {self.image} did not report its Python and "
                                 "pytest versions.") from exc
        if not str(found.get("python_version", "")).startswith("3.11."):
            raise SandboxRefused(f"The image {self.image} runs Python "
                                 f"{found.get('python_version')}; RG10 execution needs "
                                 "Python 3.11.x.")
        limits = self.limits
        return {"image_digest": digest, "python_version": found["python_version"],
                "pytest_version": found["pytest_version"],
                "import_timeout_s": limits.import_timeout_s,
                "test_timeout_s": limits.test_timeout_s, "cpu_limit": limits.cpu_limit,
                "memory_limit_mb": limits.memory_limit_mb, "network": "none"}

    def execute(self, job: Job) -> dict | None:
        """Run the runner on ``job`` in a new container and return its result, or None."""
        name = f"rg10-run-{secrets.token_hex(6)}"
        nonce = secrets.token_hex(16)
        line = self.container_command(
            name, ((job.tree.resolve(), CONTAINER_TREE), (RUNNER.resolve(), CONTAINER_RUNNER)),
            ["python", "-I", CONTAINER_RUNNER, CONTAINER_TREE, CONTAINER_SCRATCH])
        try:
            done = self._run(line, input=job_line(job, self.limits, nonce),
                             capture_output=True, text=True, check=False,
                             timeout=overall_timeout(job, self.limits))
        except subprocess.TimeoutExpired:
            self._run([self._docker, "kill", name], capture_output=True, check=False)
            return None
        return parse_reply(done.stdout, nonce)


def job_line(job: Job, limits: Limits, nonce: str) -> str:
    """Return the JSON line the runner reads from standard input."""
    return json.dumps({"nonce": nonce, "target_module": job.target_module,
                       "tests": [{"suite": suite, "test_id": test_id}
                                 for suite, test_id in job.tests],
                       "import_timeout_s": limits.import_timeout_s,
                       "test_timeout_s": limits.test_timeout_s}) + "\n"


def overall_timeout(job: Job, limits: Limits) -> int:
    """Return the longest a job's container may take: every time-out plus the overhead."""
    return (limits.import_timeout_s + len(job.tests) * (limits.test_timeout_s + 5)
            + limits.overhead_s)


def parse_reply(stdout: str, nonce: str) -> dict | None:
    """Return the result object from the runner's reply line, or None if there is none."""
    replies = [line[len(nonce):] for line in stdout.splitlines() if line.startswith(nonce)]
    if len(replies) != 1:
        return None
    try:
        result = json.loads(replies[0])
    except ValueError:
        return None
    return result if isinstance(result, dict) else None
