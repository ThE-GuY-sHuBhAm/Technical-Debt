"""Tests for the NFR-12 gate in tools/check_pylint_scores.py.

Every sample module is written to a temporary directory at test time, so no committed
file needs a Pylint exception. File names differ between tests because Pylint and
astroid cache modules by name within one process.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pylint.lint import Run

import check_pylint_scores as gate

CLEAN = '''"""A clean module."""


def double(value):
    """Return twice the value."""
    return value * 2
'''

# Three unused imports (W0611) over twelve statements: 10 - 3/12 * 10 = 7.5.
SCORES_7_5 = (
    '"""Three warnings over twelve statements."""\n\nimport os\nimport re\nimport sys\n\n'
    + "".join(f"VALUE_{index} = {index}\n" for index in range(9))
)

DUPLICATED_BODY = '''"""Module {name}."""


def compute_{name}(values):
    """Compute a total."""
    total = 0
    for value in values:
        if value > 10:
            total += value * 2
        else:
            total -= value
    result = total * 3
    result = result + 7
    return result
'''


def _write(path: Path, text: str) -> Path:
    """Create ``path`` with ``text`` and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _pylint_global_note(path: Path, tmp_path: Path) -> float:
    """Return Pylint's own score for a single file with the default configuration."""
    rcfile = _write(tmp_path / "empty-rc" / "pylintrc", "")
    run = Run([f"--rcfile={rcfile}", str(path)], exit=False)
    return run.linter.stats.global_note


def test_project_files_skip_tool_and_build_directories(tmp_path: Path) -> None:
    """Only project sources are collected; environments, caches and build output are not."""
    keep = [_write(tmp_path / "top.py", ""), _write(tmp_path / "pkg" / "inner.py", "")]
    for skipped in (".venv/lib/a.py", ".git/hooks/b.py", "__pycache__/c.py", "build/d.py",
                    "dist/e.py", "node_modules/f.py", "tdea.egg-info/g.py", "env/h.py"):
        _write(tmp_path / skipped, "")
    _write(tmp_path / "env" / "pyvenv.cfg", "home = /usr/bin\n")
    _write(tmp_path / "notes.txt", "")
    assert gate.project_files(tmp_path) == sorted(path.resolve() for path in keep)


def test_clean_file_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file with no messages scores 10.0 and the gate exits 0."""
    _write(tmp_path / "clean_sample.py", CLEAN)
    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_PASS
    assert "10.00  ok" in capsys.readouterr().out


def test_score_matches_pylint_for_a_single_file(tmp_path: Path) -> None:
    """The per-file score equals Pylint's own score when the file is linted alone."""
    path = _write(tmp_path / "src" / "warned_sample.py", SCORES_7_5).resolve()
    result = gate.score_files([path])
    item = result.files[path]
    assert item.statements == 12
    assert item.counts["warning"] == 3
    assert item.score == pytest.approx(7.5)
    assert item.score == pytest.approx(_pylint_global_note(path, tmp_path))


def test_file_below_minimum_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file scoring 7.5 fails the 8.0 minimum with exit 1 and is named in the report."""
    _write(tmp_path / "below_sample.py", SCORES_7_5)
    assert gate.main(["--root", str(tmp_path), "--min-score", "8.0"]) == gate.EXIT_BELOW_MINIMUM
    out = capsys.readouterr().out
    assert " 7.50  FAIL" in out
    assert "below_sample.py" in out
    assert "W0611 (unused-import)" in out


def test_duplicate_code_counts_against_every_file_it_names(tmp_path: Path) -> None:
    """R0801 is charged to both duplicated files, not to the module Pylint checked last."""
    first = _write(tmp_path / "dup_first.py", DUPLICATED_BODY.format(name="first")).resolve()
    second = _write(tmp_path / "dup_second.py", DUPLICATED_BODY.format(name="second")).resolve()
    bystander = _write(tmp_path / "zz_bystander.py", CLEAN).resolve()
    result = gate.score_files([first, second, bystander])
    assert [(message.symbol, charged) for message, charged in result.messages] == [
        ("duplicate-code", [first, second])
    ]
    assert result.files[first].counts["refactor"] == 1
    assert result.files[second].counts["refactor"] == 1
    assert result.files[bystander].counts["refactor"] == 0
    assert not result.unattributed


def test_duplicate_code_is_reported_against_the_duplicated_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed location names the duplicated files, not the last module checked."""
    _write(tmp_path / "dup_report_a.py", DUPLICATED_BODY.format(name="a"))
    _write(tmp_path / "dup_report_b.py", DUPLICATED_BODY.format(name="b"))
    _write(tmp_path / "zz_report_bystander.py", CLEAN)
    gate.main(["--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "dup_report_a.py, dup_report_b.py: R0801 (duplicate-code)" in out
    assert "zz_report_bystander.py:" not in out


def test_syntax_error_scores_zero(tmp_path: Path) -> None:
    """A file Pylint cannot parse has no statements and scores 0."""
    path = _write(tmp_path / "broken_sample.py", "def broken(:\n").resolve()
    item = gate.score_files([path]).files[path]
    assert item.counts["error"] == 1
    assert item.score == 0.0
    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_BELOW_MINIMUM


def test_empty_module_scores_ten(tmp_path: Path) -> None:
    """An empty __init__.py has no statements and no messages, so it scores 10.0."""
    path = _write(tmp_path / "emptypkg" / "__init__.py", "").resolve()
    item = gate.score_files([path]).files[path]
    assert item.statements == 0
    assert item.score == 10.0


def test_files_sharing_a_module_name_keep_their_own_counts(tmp_path: Path) -> None:
    """Two files outside packages with one module name are scored separately."""
    two = _write(tmp_path / "left" / "shared_name.py", '"""Left."""\nLEFT = 1\nMORE = 2\n')
    five = _write(tmp_path / "right" / "shared_name.py",
                  '"""Right."""\nimport os\nA = 1\nB = 2\nC = 3\nD = 4\n')
    result = gate.score_files([two, five])
    assert result.files[two.resolve()].statements == 2
    assert result.files[five.resolve()].statements == 5
    assert result.files[two.resolve()].score == 10.0
    assert result.files[five.resolve()].score == pytest.approx(8.0)


def test_project_and_user_configuration_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pylintrc, a pyproject section or PYLINTRC cannot switch the default checks off."""
    _write(tmp_path / ".pylintrc", "[MESSAGES CONTROL]\ndisable=all\n")
    _write(tmp_path / "pylintrc", "[MESSAGES CONTROL]\ndisable=all\n")
    _write(tmp_path / "pyproject.toml", '[tool.pylint."messages control"]\ndisable = ["all"]\n')
    user_rc = _write(tmp_path / "home" / "user.pylintrc", "[MESSAGES CONTROL]\ndisable=all\n")
    monkeypatch.setenv("PYLINTRC", str(user_rc))
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path / "configured_sample.py", SCORES_7_5).resolve()
    item = gate.score_files([path]).files[path]
    assert item.counts["warning"] == 3
    assert item.score == pytest.approx(7.5)


def test_no_files_cannot_check(tmp_path: Path) -> None:
    """An empty tree is reported as a failure to check, not as a pass."""
    assert gate.main(["--root", str(tmp_path)]) == gate.EXIT_CANNOT_CHECK
