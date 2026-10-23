"""Tests for the metric validation in tools/validate_metrics.py.

FX-S is analysed once through the real AnalysisService (only FR-03 is stubbed), then
the tool compares the stored rows with the radon and Pylint CLIs and recomputes the
scores. A copy of tdea.db with altered values shows that differences are found.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import make_fx_product
import pytest
from fixture_git import FixtureRepo
from product_support import FX_S_URL, analysed, local_service

import validate_metrics as tool

FX_S = "tdea-fixtures/fx-s"
SCORE_AND_RADON = ("radon", "aggregate", "score", "smell density")


@pytest.fixture(name="analysed_fx_s", scope="module")
def _analysed_fx_s(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Return (tdea.db, FX-S clone) after one analysis of FX-S."""
    root = tmp_path_factory.mktemp("validate")
    fx_s = make_fx_product.build_fx_s(root / "fx-s")
    service = local_service(root / "ws", {FX_S: fx_s.path})
    analysed(service, FX_S_URL)
    return service.store.path, fx_s.path


def differing(result: dict, prefixes: tuple[str, ...]) -> dict[str, int]:
    """Return the checks starting with one of ``prefixes`` that found differences."""
    return {name: check["differing"] for name, check in result["checks"].items()
            if name.startswith(prefixes) and check["differing"]}


def test_fx_s_radon_aggregates_and_scores_agree(analysed_fx_s: tuple[Path, Path]) -> None:
    """Every stored radon value, aggregate and score of FX-S equals its oracle."""
    db, clone = analysed_fx_s
    result = tool.validate(db, FX_S, clone, "all", ("file",))
    assert result["analysed_commits"] == 12 and result["file_rows_checked"] > 0
    assert differing(result, SCORE_AND_RADON) == {}
    assert result["checks"]["radon raw: sloc"]["compared"] == result["file_rows_checked"]
    assert result["checks"]["score: debt_score"]["compared"] == 12


def test_fx_s_pylint_counts_equal_each_file_linted_alone(
        analysed_fx_s: tuple[Path, Path]) -> None:
    """FX-S's stored Pylint counts equal Pylint run on each file alone."""
    db, clone = analysed_fx_s
    result = tool.validate(db, FX_S, clone, "all", ("file",))
    assert differing(result, ("pylint",)) == {}


def test_whole_tree_lint_resolves_the_test_modules_import(
        analysed_fx_s: tuple[Path, Path]) -> None:
    """Linted with the whole tree, tests/test_geometry.py has no import-error.

    TDEA lints a commit's new file versions together; at commit #4 that is the test
    module alone, so Pylint cannot resolve `from fxs.geometry import area` (E0401). The
    whole-tree lint can. This records the difference rather than hiding it.
    """
    db, clone = analysed_fx_s
    result = tool.validate(db, FX_S, clone, "all", ("tree",))
    examples = result["checks"]["pylint (whole tree): error"]["examples"]
    assert examples and {item["path"] for item in examples} == {"tests/test_geometry.py"}
    assert all((item["stored"], item["oracle"]) == (1, 0) for item in examples)


def test_altered_values_are_reported(analysed_fx_s: tuple[Path, Path],
                                     tmp_path: Path) -> None:
    """A changed SLOC and a changed Debt Score are found, and the exit status is 1."""
    db, clone = analysed_fx_s
    altered = tmp_path / "tdea.db"
    shutil.copy(db, altered)
    with sqlite3.connect(altered) as connection:
        connection.execute("UPDATE file_metrics SET sloc = sloc + 1 WHERE metric_id = "
                           "(SELECT min(metric_id) FROM file_metrics)")
        connection.execute("UPDATE aggregate_metrics SET debt_score = debt_score + 1 "
                           "WHERE agg_id = (SELECT max(agg_id) FROM aggregate_metrics)")
    result = tool.validate(altered, FX_S, clone, "all", ("file",))
    found = differing(result, ("radon raw: sloc", "score: debt_score"))
    assert found == {"radon raw: sloc": 1, "score: debt_score": 1}
    assert tool.main(["--db", str(altered), "--repository", FX_S, "--clone", str(clone),
                      "--commits", "1", "--pylint", "file"]) == tool.EXIT_DIFFER


def test_methods_of_nested_classes_are_noted_not_counted_as_differences(
        tmp_path: Path) -> None:
    """The radon CLI omits methods of a class inside a class; TDEA keeps them."""
    repo = FixtureRepo(tmp_path / "nested")
    repo.commit("Nested class", {"pkg/__init__.py": '"""Package."""\n',
                                 "pkg/shapes.py": (
                                     '"""Shapes."""\n\n\nclass Outer:\n    """Outer."""\n\n'
                                     '    class Inner:\n        """Inner."""\n\n'
                                     "        def area(self, side):\n"
                                     '            """Return the area."""\n'
                                     "            if side > 0:\n                return side\n"
                                     "            return 0\n")},
                author=make_fx_product.MAINTAINER, date="2026-09-01T10:00:00+00:00")
    service = local_service(tmp_path / "ws", {"tdea-fixtures/nested": repo.path})
    analysed(service, "https://github.com/tdea-fixtures/nested")
    result = tool.validate(service.store.path, "tdea-fixtures/nested", repo.path, "all",
                           ("file",))
    assert result["notes"] == {"functions in nested classes (not listed by radon CLI)": 1}
    assert differing(result, ("radon",)) == {}


def test_choose_spreads_the_commits_and_keeps_both_ends() -> None:
    """Five of 12 commits: the first, the last and three between them."""
    commits = [{"sequence": number} for number in range(1, 13)]
    assert [item["sequence"] for item in tool.choose(commits, "5")] == [1, 4, 7, 9, 12]
    assert tool.choose(commits, "all") == commits
    assert tool.choose(commits, "40") == commits


def test_unusable_inputs_exit_2(analysed_fx_s: tuple[Path, Path], tmp_path: Path) -> None:
    """A missing database or a repository that was not analysed gives exit status 2."""
    db, clone = analysed_fx_s
    assert tool.main(["--db", str(tmp_path / "none.db"), "--repository", FX_S,
                      "--clone", str(clone)]) == tool.EXIT_UNUSABLE
    assert tool.main(["--db", str(db), "--repository", "tdea-fixtures/other",
                      "--clone", str(clone)]) == tool.EXIT_UNUSABLE
