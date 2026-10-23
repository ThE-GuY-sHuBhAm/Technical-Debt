"""The sandbox runner: RG10 pipeline step 4, run only inside the harness's sandbox.

The host side of the harness (rg10_harness.sandbox) starts this script in a container with
no network, a read-only root, resource limits and an unprivileged user, and never runs
model code itself. TDEA never runs this script either (NFR-26, Freeze Decision 1): it
reads the execution records the harness writes.

Usage: ``python -I runner.py TREE SCRATCH`` with one JSON line on standard input:
``{"nonce", "target_module", "tests": [{"suite", "test_id"}], "import_timeout_s",
"test_timeout_s"}``. TREE is the executed tree (variant copy, modified module, hidden tests
under .hidden_tests/<task_id>/), mounted read-only; it is copied to SCRATCH/run first, so
tests that write files can. The result is one line on standard output: the nonce followed
by a JSON object ``{"python_version", "parses", "import_status", "import_error",
"tests"}``. Model code runs only in child processes whose output is captured, so it cannot
print a result line of its own; the nonce comes from standard input, not the command line.

Steps (Freeze section 9 and the execution-record table):

* Parse the module under this interpreter (Python 3.11 in the image). A module that does
  not parse gets ``parses: false`` and import status not_attempted; the host writes no
  record for it, since FR-59 needs none.
* Import it in a child process, from the tree root, with the import time-out. Exit status
  0 is "ok"; otherwise "error", with the last line of the child's traceback ("Type:
  message") as import_error. Readings [R]: a time-out is "error" with import_error
  "TimeoutError: import did not finish within N s"; a child killed by a signal (for
  example the memory limit) is "error" naming the signal.
* Only after an "ok" import, run each declared test in its own pytest child process,
  hidden tests first and then project tests, in the declared order, with the test
  time-out. Outcome from pytest's JUnit XML report: a <failure> is fail, an <error> is
  error, a passed test is pass; a test that was not collected (a collection error or an
  unknown ID) is error, as the Freeze counts a collection error as "error" for each test
  it prevented; a time-out is timeout. Reading [R]: a skipped test is error, since it
  neither passed nor failed. duration_s is the report's time, or the elapsed time when
  the report has none.

Child processes get a minimal environment and start in their own session, so a time-out
kills the whole process group. Standard library only; the image supplies pytest.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

IMPORT_SNIPPET = "import importlib, sys; importlib.import_module(sys.argv[1])"
OK, ERROR, NOT_ATTEMPTED = "ok", "error", "not_attempted"
PASS, FAIL, TIMEOUT = "pass", "fail", "timeout"
ERROR_TEXT_LIMIT = 500
CHILD_ENVIRONMENT = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                     "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def module_name(target_module: str) -> str:
    """Return the dotted import name of a module path such as pkg/mod.py."""
    parts = target_module[:-3].split("/") if target_module.endswith(".py") \
        else target_module.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def parses(source: str, filename: str) -> bool:
    """Return whether ``source`` parses under this interpreter."""
    try:
        ast.parse(source, filename=filename)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    return True


def child_environment(scratch: Path) -> dict[str, str]:
    """Return the environment for a child process: nothing from the caller but PATH."""
    environment = dict(CHILD_ENVIRONMENT)
    environment.update(PATH=os.environ.get("PATH", os.defpath), HOME=str(scratch),
                       TMPDIR=str(scratch))
    if os.name == "nt":  # needed to start Python at all on Windows (host tests only)
        environment["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return environment


def run_child(command: list[str], cwd: Path, timeout: float,
              environment: dict[str, str]) -> tuple[int | None, str, float]:
    """Run ``command``; return (exit status or None on time-out, stderr, elapsed seconds)."""
    started = time.monotonic()
    with subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True) as process:
        try:
            _out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(process)
            process.communicate()
            return None, "", time.monotonic() - started
        return process.returncode, err.decode("utf-8", "replace"), time.monotonic() - started


def _kill(process: subprocess.Popen) -> None:
    """Kill a child and, on POSIX, every process in its session."""
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
    process.kill()


def import_module(tree: Path, target_module: str, timeout: float,
                  environment: dict[str, str]) -> tuple[str, str | None]:
    """Import the module in a child process; return (import status, import error)."""
    status, err, _elapsed = run_child(
        [sys.executable, "-c", IMPORT_SNIPPET, module_name(target_module)], tree, timeout,
        environment)
    if status is None:
        return ERROR, f"TimeoutError: import did not finish within {timeout:g} s"
    if status == 0:
        return OK, None
    if status < 0:
        return ERROR, f"Killed: the import process ended on signal {abs(status)}"
    lines = [line.strip() for line in err.splitlines() if line.strip()]
    return ERROR, (lines[-1] if lines else f"exit status {status}")[:ERROR_TEXT_LIMIT]


def run_test(tree: Path, scratch: Path, test_id: str, timeout: float,
             environment: dict[str, str]) -> tuple[str, float]:
    """Run one test in its own pytest process; return (outcome, duration in seconds)."""
    report = scratch / "report.xml"
    report.unlink(missing_ok=True)
    status, _err, elapsed = run_child(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"--junitxml={report}", "-o", "junit_family=xunit2", test_id],
        tree, timeout, environment)
    if status is None:
        return TIMEOUT, round(elapsed, 3)
    return junit_outcome(report, elapsed)


def junit_outcome(report: Path, elapsed: float) -> tuple[str, float]:
    """Return the outcome and duration of the one test a JUnit XML report covers."""
    try:
        cases = ElementTree.parse(report).getroot().iter("testcase")
        cases = list(cases)
    except (OSError, ElementTree.ParseError):
        return ERROR, round(elapsed, 3)
    if not cases:
        return ERROR, round(elapsed, 3)  # not collected: a collection error or unknown ID
    outcome = PASS
    for case in cases:
        if case.find("error") is not None or case.find("skipped") is not None:
            outcome = ERROR
        elif case.find("failure") is not None and outcome == PASS:
            outcome = FAIL
    try:
        duration = sum(float(case.get("time", "")) for case in cases)
    except ValueError:
        duration = elapsed
    return outcome, round(duration, 3)


def execute(job: dict, tree: Path, scratch: Path) -> dict:
    """Run the job on a writable copy of ``tree`` and return the result object."""
    run = scratch / "run"
    shutil.copytree(tree, run)
    environment = child_environment(scratch)
    target = job["target_module"]
    source = (run / target).read_bytes().decode("utf-8")
    result: dict = {"python_version": platform.python_version(), "parses": True,
                    "import_status": NOT_ATTEMPTED, "import_error": None, "tests": []}
    if not parses(source, target):
        result["parses"] = False
        return result
    status, error = import_module(run, target, float(job["import_timeout_s"]), environment)
    result.update(import_status=status, import_error=error)
    if status != OK:
        return result
    for test in job["tests"]:
        outcome, duration = run_test(run, scratch, test["test_id"],
                                     float(job["test_timeout_s"]), environment)
        result["tests"].append({"suite": test["suite"], "test_id": test["test_id"],
                                "outcome": outcome, "duration_s": duration})
    return result


def main(argv: list[str]) -> int:
    """Read the job from standard input, execute it and print the result line."""
    tree, scratch = Path(argv[1]), Path(argv[2])
    job = json.loads(sys.stdin.readline())
    nonce = job.pop("nonce")
    sys.stdin.close()
    result = execute(job, tree, scratch)
    sys.stdout.write(nonce + json.dumps(result, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
