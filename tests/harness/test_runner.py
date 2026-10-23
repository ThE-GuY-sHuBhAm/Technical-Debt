"""The sandbox runner (RG10 pipeline step 4): parse, import and per-test outcomes.

The runner is run as the Docker sandbox would run it, but in a child process on the test
machine (LocalSandbox), on small trees written here by hand. Nothing in these trees is
model output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rg10_harness_fixture import LocalSandbox

from rg10_harness.runner import junit_outcome, module_name, parses
from rg10_harness.sandbox import Job, Limits

LIMITS = Limits(import_timeout_s=3, test_timeout_s=3)
GOOD = "def discounted(price, rate):\n    return price * (1 - rate)\n"


def tree(root: Path, module: str, tests: dict[str, str]) -> Path:
    """Write a tree with package shop (module shop/orders.py) and the given test files."""
    (root / "shop").mkdir(parents=True)
    (root / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (root / "shop" / "orders.py").write_text(module, encoding="utf-8")
    for name, text in tests.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def run(root: Path, tests: tuple[tuple[str, str], ...] = ()) -> dict:
    """Execute the tree's module and tests through the runner and return its result."""
    result = LocalSandbox(LIMITS).execute(Job(root, "shop/orders.py", tests))
    assert result is not None
    return result


def outcomes(result: dict) -> list[tuple[str, str, str]]:
    """Return (suite, test_id, outcome) per recorded test."""
    return [(test["suite"], test["test_id"], test["outcome"]) for test in result["tests"]]


def test_module_names() -> None:
    """A module path becomes its dotted import name; a package's __init__ its package."""
    assert module_name("shop/orders.py") == "shop.orders"
    assert module_name("shop/__init__.py") == "shop"
    assert module_name("orders.py") == "orders"


def test_parse_check() -> None:
    """A syntax error or excessive nesting is "does not parse", never a crash."""
    assert parses(GOOD, "m.py")
    assert not parses("def f(:\n", "m.py")
    assert not parses("x = " + "(" * 10_000 + ")" * 10_000 + "\n", "m.py")


def test_outcomes_of_one_run(tmp_path: Path) -> None:
    """Pass, fail, a collection error, an unknown ID and a skip, each from its report."""
    root = tree(tmp_path / "t", GOOD, {
        "tests/test_a.py": ("import pytest\nfrom shop.orders import discounted\n\n\n"
                            "def test_pass():\n    assert discounted(100, 0.25) == 75\n\n\n"
                            "def test_fail():\n    assert discounted(1, 0) == 2\n\n\n"
                            "def test_skip():\n    pytest.skip('no')\n"),
        "tests/test_broken.py": "def test_x(:\n"})
    tests = (("hidden", "tests/test_a.py::test_pass"), ("hidden", "tests/test_a.py::test_fail"),
             ("project", "tests/test_broken.py::test_x"),
             ("project", "tests/test_a.py::test_missing"),
             ("project", "tests/test_a.py::test_skip"))
    result = run(root, tests)
    assert (result["parses"], result["import_status"], result["import_error"]) == (
        True, "ok", None)
    assert [outcome for _suite, _id, outcome in outcomes(result)] == [
        "pass", "fail", "error", "error", "error"]
    assert [(suite, test_id) for suite, test_id, _outcome in outcomes(result)] == list(tests)
    assert all(isinstance(test["duration_s"], float) and test["duration_s"] >= 0
               for test in result["tests"])
    assert result["python_version"].count(".") == 2


def test_an_import_error_is_named_and_no_test_runs(tmp_path: Path) -> None:
    """A failed import is "error" with the exception's type and message; tests are skipped."""
    root = tree(tmp_path / "t", "import rg10_no_such_module\n", {})
    result = run(root, (("hidden", "tests/test_a.py::test_pass"),))
    assert result["import_status"] == "error" and result["tests"] == []
    assert result["import_error"] == "ModuleNotFoundError: No module named 'rg10_no_such_module'"


def test_an_import_that_never_ends_is_stopped(tmp_path: Path) -> None:
    """An import beyond the import time-out is an error naming the time-out."""
    root = tree(tmp_path / "t", "while True:\n    pass\n", {})
    result = run(root)
    assert result["import_status"] == "error"
    assert result["import_error"] == "TimeoutError: import did not finish within 3 s"


def test_a_test_that_never_ends_is_a_timeout(tmp_path: Path) -> None:
    """A test beyond the test time-out is "timeout"; the next test still runs."""
    root = tree(tmp_path / "t", GOOD, {
        "tests/test_a.py": ("def test_slow():\n    while True:\n        pass\n\n\n"
                            "def test_quick():\n    assert True\n")})
    result = run(root, (("hidden", "tests/test_a.py::test_slow"),
                        ("hidden", "tests/test_a.py::test_quick")))
    assert [outcome for _suite, _id, outcome in outcomes(result)] == ["timeout", "pass"]


def test_a_module_that_does_not_parse_is_not_imported(tmp_path: Path) -> None:
    """A module that does not parse gets parses false and import status not_attempted."""
    sentinel = tmp_path / "imported"
    root = tree(tmp_path / "t", f"open({str(sentinel)!r}, 'w').close()\ndef f(:\n", {})
    result = run(root, (("hidden", "tests/test_a.py::test_pass"),))
    assert (result["parses"], result["import_status"], result["tests"]) == (
        False, "not_attempted", [])
    assert not sentinel.exists()


def test_children_see_no_variable_from_the_caller(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Imports and tests run with a minimal environment: a caller's variable is absent."""
    monkeypatch.setenv("RG10_CANARY", "secret")
    root = tree(tmp_path / "t", GOOD, {
        "tests/test_env.py": ("import os\n\n\ndef test_env():\n"
                              "    assert 'RG10_CANARY' not in os.environ\n")})
    result = run(root, (("project", "tests/test_env.py::test_env"),))
    assert outcomes(result) == [("project", "tests/test_env.py::test_env", "pass")]


def test_the_tree_itself_is_not_changed(tmp_path: Path) -> None:
    """The runner works on a copy, so a test that writes a file leaves the tree as it was."""
    root = tree(tmp_path / "t", GOOD, {
        "tests/test_w.py": ("from pathlib import Path\n\n\ndef test_w():\n"
                            "    Path('written.txt').write_text('x')\n")})
    before = sorted(path.relative_to(root) for path in root.rglob("*"))
    assert outcomes(run(root, (("project", "tests/test_w.py::test_w"),)))[0][2] == "pass"
    assert sorted(path.relative_to(root) for path in root.rglob("*")) == before


@pytest.mark.parametrize("xml, outcome", [
    ('<testsuites><testsuite><testcase name="t" time="0.5"/></testsuite></testsuites>', "pass"),
    ('<testsuite><testcase name="t" time="0.1"><failure/></testcase></testsuite>', "fail"),
    ('<testsuite><testcase name="t"><error/></testcase></testsuite>', "error"),
    ('<testsuite><testcase name="t"><skipped/></testcase></testsuite>', "error"),
    ("<testsuite></testsuite>", "error"),
    ("not xml", "error"),
])
def test_junit_reports(tmp_path: Path, xml: str, outcome: str) -> None:
    """The outcome read from a JUnit XML report; a missing time falls back to elapsed."""
    report = tmp_path / "r.xml"
    report.write_text(xml, encoding="utf-8")
    found, duration = junit_outcome(report, 2.0)
    assert found == outcome and duration >= 0


def test_a_missing_report_is_an_error(tmp_path: Path) -> None:
    """No report at all (pytest could not start) is "error"."""
    assert junit_outcome(tmp_path / "absent.xml", 1.25) == ("error", 1.25)
