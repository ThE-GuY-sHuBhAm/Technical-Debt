"""FX-S and FX-ERR: the two product fixtures the RE checks reuse (W-05, Day 2 part).

FX-S: 12 commits, all on main, and 3 branches (main, develop, feature/charts). It also
carries the workbook's commit-history data: a 120-character message (TC-021), a commit
made at 10 Sep 2026 18:30 +05:30 (TC-023), and one commit that adds a.py, modifies b.py
and deletes c.py (TC-024).

FX-ERR: one commit with a syntax-error, a Latin-1, an empty and a binary .py file, plus
one valid file (RE-04 counts the failing files).

Usage: python make_fx_product.py fx-s|fx-err OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from fixture_git import FixtureRepo, Identity

VERSION = "1"
MAINTAINER = Identity("Meera Iyer", "meera.iyer@example.invalid")
REVIEWER = Identity("Kabir Shah", "kabir.shah@example.invalid")

LONG_MESSAGE = ("Rework the statistics helpers so that each function documents its input, "
                "handles empty sequences and rounds consistently")  # 120 characters (TC-021)
TC023_DATE = "2026-09-10T18:30:00+05:30"
FXS_BRANCHES = {"develop": 8, "feature/charts": 10}  # branch -> commit number it points at


@dataclass(frozen=True)
class BuiltFixture:
    """A built fixture repository and its commit SHAs, oldest first."""

    path: Path
    shas: tuple[str, ...]
    version: str = VERSION


def _module(name: str, body: str) -> str:
    """Return a small module with a docstring and ``body``."""
    return f'"""{name} module of the FX-S fixture."""\n\n\n{body}'


def _fxs_date(number: int) -> str:
    """Return commit ``number``'s date: one a day from 1 Sep 2026, commit 9 at TC-023's time."""
    if number == 9:
        return TC023_DATE
    day = number if number < 9 else number + 1
    return f"2026-09-{day:02d}T10:00:00+05:30"


def build_fx_s(path: Path) -> BuiltFixture:
    """Build FX-S at ``path`` (which must not exist)."""
    repo = FixtureRepo(path)
    steps: list[tuple[str, dict[str, str | bytes | None], Identity]] = [
        ("Create the fxs package", {
            "fxs/__init__.py": '"""FX-S product fixture."""\n',
            "fxs/b.py": _module("b", "VALUE = 1\n"),
            "fxs/c.py": _module("c", "VALUE = 2\n"),
            "fxs/geometry.py": _module("geometry", "def area(width, height):\n"
                                                   "    return width * height\n"),
        }, MAINTAINER),
        ("Add statistics helpers", {
            "fxs/stats.py": _module("stats", "def total(values):\n    return sum(values)\n"),
        }, MAINTAINER),
        ("Add perimeter", {
            "fxs/geometry.py": _module("geometry", "def area(width, height):\n"
                                                   "    return width * height\n\n\n"
                                                   "def perimeter(width, height):\n"
                                                   "    return 2 * (width + height)\n"),
        }, REVIEWER),
        ("Add geometry tests", {
            "tests/test_geometry.py": "from fxs.geometry import area\n\n\n"
                                      "def test_area():\n    assert area(2, 3) == 6\n",
        }, REVIEWER),
        ("Add mean", {
            "fxs/stats.py": _module("stats", "def total(values):\n    return sum(values)\n\n\n"
                                             "def mean(values):\n"
                                             "    return total(values) / len(values)\n"),
        }, MAINTAINER),
        (LONG_MESSAGE, {
            "fxs/stats.py": _module("stats", "def total(values):\n    return sum(values)\n\n\n"
                                             "def mean(values):\n"
                                             "    if not values:\n        return 0.0\n"
                                             "    return round(total(values) / len(values), 2)\n"),
        }, MAINTAINER),
        ("Add report", {
            "fxs/report.py": _module("report", "def header():\n    return 'FX-S report'\n"),
        }, REVIEWER),
        ("Add report footer", {
            "fxs/report.py": _module("report", "def header():\n    return 'FX-S report'\n\n\n"
                                               "def footer():\n    return 'end'\n"),
        }, MAINTAINER),
        ("Show the report date", {
            "fxs/report.py": _module("report", "def header():\n    return 'FX-S report'\n\n\n"
                                               "def footer():\n    return 'end of report'\n"),
        }, MAINTAINER),
        ("Replace c with a", {
            "fxs/a.py": _module("a", "VALUE = 3\n"),
            "fxs/b.py": _module("b", "VALUE = 10\n"),
            "fxs/c.py": None,
        }, REVIEWER),
        ("Add volume", {
            "fxs/geometry.py": _module("geometry", "def area(width, height):\n"
                                                   "    return width * height\n\n\n"
                                                   "def perimeter(width, height):\n"
                                                   "    return 2 * (width + height)\n\n\n"
                                                   "def volume(width, height, depth):\n"
                                                   "    return area(width, height) * depth\n"),
        }, MAINTAINER),
        ("Number report lines", {
            "fxs/report.py": _module("report", "def header():\n    return '1 FX-S report'\n\n\n"
                                               "def footer():\n    return '2 end of report'\n"),
        }, MAINTAINER),
    ]
    shas = []
    for number, (message, files, author) in enumerate(steps, start=1):
        shas.append(repo.commit(message, files, author=author, date=_fxs_date(number)))
    for branch, number in FXS_BRANCHES.items():
        repo.git("branch", branch, shas[number - 1])
    return BuiltFixture(Path(path), tuple(shas))


def build_fx_err(path: Path) -> BuiltFixture:
    """Build FX-ERR at ``path`` (which must not exist)."""
    repo = FixtureRepo(path)
    sha = repo.commit("Add files that fail to analyse", {
        "fxerr/valid.py": '"""A valid module."""\n\nVALUE = 1\n',
        "fxerr/syntax_error.py": "def broken(:\n    return 1\n",
        "fxerr/latin1.py": b'"""Latin-1 text without a coding line."""\n\nNAME = "caf\xe9"\n',
        "fxerr/empty.py": b"",
        "fxerr/binary.py": b"\x7fELF\x02\x01\x01\x00\x00\x00\xff\xfe\x00binary\x00\x01",
    }, author=MAINTAINER, date="2026-09-01T10:00:00+05:30")
    return BuiltFixture(Path(path), (sha,))


def main(argv: list[str] | None = None) -> int:
    """Build FX-S or FX-ERR into the given directory and print the SHAs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fixture", choices=["fx-s", "fx-err"])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    args = parser.parse_args(argv)
    builder = build_fx_s if args.fixture == "fx-s" else build_fx_err
    for sha in builder(args.output).shas:
        print(sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())