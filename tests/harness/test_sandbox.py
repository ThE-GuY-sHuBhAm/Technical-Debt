"""DockerSandbox: the container's isolation flags, the fingerprint and the reply framing.

No container is started here: the docker command line is replaced by a recorder, so these
tests check what the harness would ask Docker to do.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rg10_harness.sandbox import (RUNNER, DockerSandbox, Job, Limits, SandboxRefused,
                                  job_line, overall_timeout, parse_reply)

DIGEST = "sha256:" + "cd" * 32


class Recorder:
    """Stands in for subprocess.run: records each call and answers from a script."""

    def __init__(self, *answers: object) -> None:
        """Answer the calls in order; an exception is raised instead of returned."""
        self.answers = list(answers)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess:
        """Record the call and give the next answer."""
        self.calls.append((list(command), options))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Return a finished process with ``stdout``."""
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def versions(python: str = "3.11.11") -> subprocess.CompletedProcess:
    """Return the fingerprint probe's reply."""
    return done(json.dumps({"python_version": python, "pytest_version": "8.3.3"}) + "\n")


def test_the_container_is_isolated(tmp_path: Path) -> None:
    """No network, read-only root and mounts, limits, no capabilities, user nobody."""
    sandbox = DockerSandbox("img", Limits(cpu_limit=2, memory_limit_mb=512, scratch_mb=64))
    line = sandbox.container_command("n", ((tmp_path, "/work"),), ["python", "-I", "x"])
    text = " ".join(line)
    for flag in ("--rm", "--network none", "--read-only", "--cpus 2", "--memory 512m",
                 "--memory-swap 512m", "--pids-limit 256", "--cap-drop ALL",
                 "--security-opt no-new-privileges", "--user 65534:65534",
                 "--tmpfs /tmp:rw,noexec,nosuid,size=64m",
                 f"--mount type=bind,source={tmp_path},target=/work,readonly"):
        assert flag in text
    assert line[-4:] == ["img", "python", "-I", "x"]
    environment = [line[i + 1] for i, part in enumerate(line) if part in ("--env", "-e")]
    assert environment == ["HOME=/tmp"] and "--env-file" not in line
    assert "--privileged" not in line and "--volume" not in line and "-v" not in line


def test_the_fingerprint_names_the_image_and_limits() -> None:
    """Image digest, Python and pytest versions, time-outs, CPU, memory and network none."""
    run = Recorder(done(DIGEST + "\n"), versions())
    found = DockerSandbox("img", Limits(), run=run).fingerprint()
    assert found == {"image_digest": DIGEST, "python_version": "3.11.11",
                     "pytest_version": "8.3.3", "import_timeout_s": 10, "test_timeout_s": 30,
                     "cpu_limit": 1, "memory_limit_mb": 1024, "network": "none"}
    probe = run.calls[1][0]
    assert "--network" in probe and probe[probe.index("--network") + 1] == "none"


def test_a_sandbox_without_python_311_is_refused() -> None:
    """Freeze Decision 1, condition 2: only Python 3.11.x executes outputs."""
    with pytest.raises(SandboxRefused, match="Python 3.12.1"):
        DockerSandbox("img", run=Recorder(done(DIGEST), versions("3.12.1"))).fingerprint()


@pytest.mark.parametrize("inspected", [done("", 1), done("not-a-digest")])
def test_an_image_that_is_not_there_is_refused(inspected: subprocess.CompletedProcess) -> None:
    """Without a local image digest, nothing is started."""
    run = Recorder(inspected)
    with pytest.raises(SandboxRefused, match="not available"):
        DockerSandbox("img", run=run).fingerprint()
    assert len(run.calls) == 1


def test_a_job_runs_the_runner_with_the_tree_read_only(tmp_path: Path) -> None:
    """The tree and the runner are mounted read-only; the job goes in on standard input."""
    job = Job(tmp_path, "shop/orders.py", (("hidden", "t.py::a"), ("project", "u.py::b")))

    def reply(command: list[str], **options: object) -> subprocess.CompletedProcess:
        nonce = json.loads(str(options["input"]))["nonce"]
        assert nonce not in " ".join(command)  # the nonce is not on the command line
        return done(f"noise\n{nonce}" + json.dumps({"parses": True}) + "\n")

    calls: list[list[str]] = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        calls.append(command)
        assert options["timeout"] == overall_timeout(job, Limits()) == 10 + 2 * 35 + 60
        return reply(command, **options)

    assert DockerSandbox("img", run=run).execute(job) == {"parses": True}
    text = " ".join(calls[0])
    assert f"source={tmp_path.resolve()},target=/work,readonly" in text
    assert f"source={RUNNER.resolve()},target=/harness/runner.py,readonly" in text
    assert calls[0][-5:] == ["python", "-I", "/harness/runner.py", "/work", "/tmp"]


def test_a_container_that_overruns_is_killed_and_gives_no_result(tmp_path: Path) -> None:
    """A time-out kills the named container; the output then gets no record."""
    run = Recorder(subprocess.TimeoutExpired("docker", 1), done())
    assert DockerSandbox("img", run=run).execute(Job(tmp_path, "m.py", ())) is None
    name = run.calls[0][0][run.calls[0][0].index("--name") + 1]
    assert run.calls[1][0] == ["docker", "kill", name]


def test_only_a_line_with_the_nonce_is_a_reply() -> None:
    """Printed text, a forged line without the nonce, two replies or bad JSON give None."""
    assert parse_reply('{"parses": true}\nN{"parses": false}\n', "N") == {"parses": False}
    assert parse_reply('{"parses": true}\n', "N") is None
    assert parse_reply('N{"a": 1}\nN{"a": 2}\n', "N") is None
    assert parse_reply("N{not json\n", "N") is None
    assert parse_reply("N[1]\n", "N") is None


def test_the_job_line_carries_the_time_outs(tmp_path: Path) -> None:
    """The runner gets the module, the declared tests in order and both time-outs."""
    line = job_line(Job(tmp_path, "m.py", (("hidden", "a"),)), Limits(3, 4), "N")
    assert line.endswith("\n") and json.loads(line) == {
        "nonce": "N", "target_module": "m.py", "tests": [{"suite": "hidden", "test_id": "a"}],
        "import_timeout_s": 3, "test_timeout_s": 4}
