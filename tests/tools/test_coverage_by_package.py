"""Tests for the NFR-13 per-package report in tools/coverage_by_package.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import coverage_by_package as report


def _summary(statements: int, covered: int) -> dict[str, dict[str, int]]:
    """Return a coverage.py file entry with the given totals."""
    return {"summary": {"num_statements": statements, "covered_lines": covered}}


def _report(tmp_path: Path, files: dict[str, dict[str, dict[str, int]]]) -> Path:
    """Write a coverage JSON report and return its path."""
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("path", "package"),
    [
        ("tdea/api/cli.py", "api"),
        ("tdea/__init__.py", "tdea"),
        ("tdea/analyzer/research/engine.py", "analyzer"),
        ("C:\\work\\tdea\\tdea\\aggregator\\research_job.py", "aggregator"),
        ("/home/ci/tdea/tdea/git_handler/errors.py", "git_handler"),
        ("tests/product/test_cli.py", None),
    ],
)
def test_package_of(path: str, package: str | None) -> None:
    """Files map to the first directory under tdea/, research modules included."""
    assert report.package_of(path) == package


def test_by_package_adds_files_up() -> None:
    """Statement and covered counts are summed per package, not averaged."""
    totals = report.by_package({"files": {
        "tdea/analyzer/a.py": _summary(10, 10),
        "tdea/analyzer/research/b.py": _summary(30, 15),
        "tdea/api/cli.py": _summary(4, 2),
    }})
    assert (totals["analyzer"].statements, totals["analyzer"].covered) == (40, 25)
    assert totals["analyzer"].percent == pytest.approx(62.5)
    assert totals["api"].percent == pytest.approx(50.0)


def test_report_only_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without --fail-under low coverage still exits 0, and empty packages are shown."""
    path = _report(tmp_path, {"tdea/analyzer/a.py": _summary(10, 1)})
    assert report.main([str(path)]) == report.EXIT_PASS
    out = capsys.readouterr().out
    assert "tdea.analyzer" in out and "10.0%" in out
    assert "tdea.git_handler" in out and "no statements" in out


def test_gate_fails_a_package_below_threshold(tmp_path: Path) -> None:
    """With --fail-under 70 a required package at 69% fails."""
    path = _report(tmp_path, {
        "tdea/git_handler/a.py": _summary(100, 70),
        "tdea/analyzer/a.py": _summary(100, 69),
        "tdea/aggregator/a.py": _summary(100, 90),
    })
    assert report.main([str(path), "--fail-under", "70"]) == report.EXIT_BELOW_THRESHOLD


def test_gate_passes_when_every_required_package_meets_it(tmp_path: Path) -> None:
    """Packages outside --require do not affect the gate."""
    path = _report(tmp_path, {
        "tdea/git_handler/a.py": _summary(100, 70),
        "tdea/analyzer/a.py": _summary(100, 80),
        "tdea/aggregator/a.py": _summary(100, 90),
        "tdea/api/cli.py": _summary(100, 0),
    })
    assert report.main([str(path), "--fail-under", "70"]) == report.EXIT_PASS


def test_gate_fails_a_required_package_with_no_statements(tmp_path: Path) -> None:
    """A required package with no code cannot meet the gate."""
    path = _report(tmp_path, {
        "tdea/git_handler/a.py": _summary(100, 100),
        "tdea/analyzer/a.py": _summary(100, 100),
    })
    assert report.main([str(path), "--fail-under", "70"]) == report.EXIT_BELOW_THRESHOLD


def test_unreadable_report(tmp_path: Path) -> None:
    """A missing or malformed report exits 2."""
    assert report.main([str(tmp_path / "missing.json")]) == report.EXIT_CANNOT_READ
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert report.main([str(bad)]) == report.EXIT_CANNOT_READ
