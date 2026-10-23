"""FR-08 sampling and FR-11 to FR-15 per-file metrics, component level (sprint 23 Oct).

Radon reads inert byte strings; Pylint runs in its isolated process on files written to
a temporary folder. Nothing is imported or run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tdea.analyzer.product_metrics import (EmptyFile, FileMetrics, PylintCounter,
                                           SmellCounts, measure)
from tdea.git_handler.commit_sampler import check_n, sample

MODULE = (b'"""A module."""\n\n\n'
          b"def plain(value):\n"
          b'    """Return the value."""\n'
          b"    return value\n\n\n"
          b"def branchy(value):\n"
          b'    """Return a label."""\n'
          b"    if value > 1:\n        return 'big'\n"
          b"    if value < 0:\n        return 'negative'\n"
          b"    return 'small'\n\n\n"
          b"class Box:\n"
          b'    """A box."""\n\n'
          b"    def size(self, flag):\n"
          b'        """Return the size."""\n'
          b"        return 2 if flag else 1\n")


def test_up_to_100_commits_are_all_analysed() -> None:
    """FR-08: sampling applies only above 100 commits."""
    commits = list(range(100))
    assert sample(commits, 5) == commits


@pytest.mark.parametrize("count, step, expected", [
    (101, 2, list(range(0, 101, 2))),
    (101, 3, list(range(0, 101, 3)) + [100]),
    (150, 1, list(range(150))),
    (250, 100, [0, 100, 200, 249]),
])
def test_every_nth_commit_with_first_and_head(count: int, step: int,
                                              expected: list[int]) -> None:
    """Every N-th commit from the first; the first and HEAD are always included."""
    assert sample(list(range(count)), step) == expected


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True, None])
def test_n_must_be_a_whole_number_of_at_least_1(value: object) -> None:
    """The minimum N is 1."""
    with pytest.raises(ValueError):
        check_n(value)


def test_radon_raw_cc_and_mi_per_file() -> None:
    """FR-11 to FR-13: LOC, SLOC, comments, blanks; CC and rank per function; MI 0-100."""
    result = measure(MODULE)
    assert (result.loc, result.sloc, result.blank) == (23, 11, 7)  # docstrings are not SLOC
    assert [(item.name, item.complexity, item.rank) for item in result.functions] == [
        ("plain", 1, "A"), ("branchy", 3, "A"), ("Box.size", 2, "A")]
    assert isinstance(result.mi, int) and 0 <= result.mi <= 100


@pytest.mark.parametrize("content, error", [
    (b"", EmptyFile), (b"def broken(:\n    return 1\n", SyntaxError),
    (b'NAME = "caf\xe9"\n', SyntaxError),
])
def test_files_that_cannot_be_measured_raise(content: bytes, error: type) -> None:
    """FR-16: empty, unparsable and undecodable files raise for isolate() to log."""
    with pytest.raises(error):
        measure(content)


def test_smell_density_and_file_cc() -> None:
    """FR-15: messages / SLOC, 0.0 for SLOC 0; mean CC over functions, 0.0 for none."""
    result = measure(MODULE)
    metrics = FileMetrics("m.py", result, SmellCounts(1, 2, 3, 4))
    assert metrics.smell_density == 10 / 11
    assert metrics.mean_cc == 2.0 and metrics.max_cc == 3 and metrics.cc_rank == "A"
    empty = FileMetrics("e.py", measure(b"# only a comment\n"), SmellCounts(1))
    assert empty.smell_density == 0.0 and empty.mean_cc == 0.0


def test_pylint_counts_by_category_in_isolation(tmp_path: Path) -> None:
    """FR-14: C, R, W and E per file; the repository's own pylintrc is never read."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "bad.py").write_bytes(
        b"import os\n\n\ndef f(x):\n    return undefined_name + x\n")
    (tmp_path / "pkg" / "good.py").write_bytes(b'"""Good."""\n\nVALUE = 1\n')
    hook = tmp_path / "hook.txt"
    (tmp_path / "pylintrc").write_text(
        f"[MAIN]\ninit-hook=open({str(hook)!r}, 'w').write('ran')\ndisable=all\n",
        encoding="utf-8")
    counts = PylintCounter().count(tmp_path, ["pkg/bad.py", "pkg/good.py"])
    bad = counts["pkg/bad.py"]
    assert bad.error >= 1 and bad.warning >= 1 and bad.convention >= 1
    assert counts["pkg/good.py"] == SmellCounts()
    assert not hook.exists()
    assert PylintCounter().count(tmp_path, []) == {}
