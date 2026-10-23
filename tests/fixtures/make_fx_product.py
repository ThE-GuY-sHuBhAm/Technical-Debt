"""The product fixtures of workbook v1.2, built by deterministic scripts (nothing is pushed).

FX-S: 12 commits, all on main, and 3 branches (main, develop, feature/charts). It also
carries the workbook's commit-history data: a 120-character message (TC-021), a commit
made at 10 Sep 2026 18:30 +05:30 (TC-023), and one commit that adds a.py, modifies b.py
and deletes c.py (TC-024).

FX-ERR: one commit with a syntax-error, a Latin-1, an empty and a binary .py file, plus
one valid file (RE-04 counts the failing files).

The others (23 Oct) are built to the workbook's fixture table, which describes them in
one line each and ships none of them:

* FX-M: 50 commits with mixed Spike / Drop / Stable changes. Four graded modules; a
  fixed 7-commit cycle raises one module's complexity and smells (spike), lowers it
  again (drop), and otherwise edits only a docstring (stable). Commit 26 adds a fifth
  module, so total SLOC varies too.
* FX-L and FX-XL: Python repositories with 200 and 500 commits; each commit changes one
  of ten graded modules.
* FX-100, FX-101, FX-102: exactly 100, 101 and 102 commits (sampling boundaries); one
  module, then one changed line of CHANGES.txt per commit.
* FX-RISK: 15 Python files, 3 of them deliberately complex and smelly.
* FX-UP and FX-DOWN: 12 commits whose debt rises (falls) at every commit. Every graded
  module has the same SLOC; its level sets its branches and unused imports. FX-UP adds a
  module of the next level at each commit; FX-DOWN starts with all twelve and deletes
  the highest level at each commit.

These builders follow the workbook's descriptions. Whether they are accepted as the
official fixtures is the Product Owner's decision (decision sheet item 4.3). FX-EMPTY
and FX-PRIV must be real GitHub repositories (an empty and a private one) and cannot be
built here.

Usage: python make_fx_product.py FIXTURE OUTPUT_DIR (builds locally; nothing is pushed).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
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


UNUSED_IMPORTS = ("os", "sys", "re", "json", "math", "time", "random", "string", "shutil",
                  "glob", "csv", "uuid")
MAX_LEVEL = len(UNUSED_IMPORTS)
START = datetime.fromisoformat("2026-09-01T10:00:00+05:30")


def _hourly(number: int) -> str:
    """Return the date of commit ``number`` (from 1): one an hour from 1 Sep 2026."""
    return (START + timedelta(hours=number - 1)).isoformat()


def graded_module(name: str, level: int, revision: int = 0) -> str:
    """Return a module whose debt grows with ``level`` (0 to MAX_LEVEL) at a fixed SLOC.

    Level L has L unused imports and one function with L if-branches; module-level
    constants pad it, so every level has the same number of lines. ``revision`` changes
    only the docstring text, which leaves every metric as it was.
    """
    if not 0 <= level <= MAX_LEVEL:
        raise ValueError(f"level must be 0 to {MAX_LEVEL}")
    lines = [f'"""Graded module {name}, revision {revision}."""']
    lines += [f"import {module}" for module in UNUSED_IMPORTS[:level]]
    lines += ["", "", f"def grade_{name}(value):", '    """Return the grade of value."""']
    for branch in range(level):
        lines += [f"    if value == {branch}:", f"        return {branch}"]
    lines += ["    return -1", "", ""]
    lines += [f"PAD_{index:02d} = {index}" for index in range(3 * (MAX_LEVEL - level))]
    return "\n".join(lines) + "\n"


def _build(path: Path, steps: list[tuple[str, dict[str, str | bytes | None]]]) -> BuiltFixture:
    """Commit each (message, files) step with MAINTAINER, one an hour."""
    repo = FixtureRepo(path)
    shas = tuple(repo.commit(message, files, author=MAINTAINER, date=_hourly(number))
                 for number, (message, files) in enumerate(steps, start=1))
    return BuiltFixture(Path(path), shas)


FXM_MODULES = ("alpha", "beta", "gamma", "delta")
FXM_BASE_LEVEL, FXM_SPIKE_LEVEL = 2, 10
FXM_CYCLE = ("stable", "stable", "spike", "stable", "stable", "drop", "stable")


def build_fx_m(path: Path) -> BuiltFixture:
    """Build FX-M: 50 commits with mixed spike, drop and stable changes."""
    levels = dict.fromkeys(FXM_MODULES, FXM_BASE_LEVEL)
    steps: list[tuple[str, dict[str, str | bytes | None]]] = [(
        "Create the fxm package",
        {"fxm/__init__.py": '"""FX-M product fixture."""\n',
         **{f"fxm/{name}.py": graded_module(name, FXM_BASE_LEVEL) for name in FXM_MODULES}})]
    for number in range(2, 51):
        kind = FXM_CYCLE[(number - 2) % len(FXM_CYCLE)]
        name = FXM_MODULES[(number - 2) // len(FXM_CYCLE) % len(FXM_MODULES)]
        if number == 26:
            levels["epsilon"] = FXM_BASE_LEVEL
            steps.append(("Add the epsilon module",
                          {"fxm/epsilon.py": graded_module("epsilon", FXM_BASE_LEVEL)}))
            continue
        if kind == "spike":
            levels[name] = FXM_SPIKE_LEVEL
            message = f"Add special cases to {name}"
        elif kind == "drop":
            levels[name] = FXM_BASE_LEVEL
            message = f"Remove the special cases from {name}"
        else:
            message = f"Reword the {name} docstring"
        steps.append((message, {f"fxm/{name}.py": graded_module(name, levels[name], number)}))
    return _build(path, steps)


def build_fx_count(path: Path, commits: int) -> BuiltFixture:
    """Build FX-100, FX-101 or FX-102: exactly ``commits`` commits."""
    steps: list[tuple[str, dict[str, str | bytes | None]]] = [(
        "Create the fxn package", {"fxn/__init__.py": '"""FX-N product fixture."""\n',
                                   "fxn/core.py": graded_module("core", 3),
                                   "CHANGES.txt": "Change 1\n"})]
    steps += [(f"Note change {number}", {"CHANGES.txt": f"Change {number}\n"})
              for number in range(2, commits + 1)]
    return _build(path, steps)


def build_fx_history(path: Path, commits: int) -> BuiltFixture:
    """Build FX-L (200 commits) or FX-XL (500): each commit changes one of ten modules."""
    names = [f"module_{index}" for index in range(10)]
    steps: list[tuple[str, dict[str, str | bytes | None]]] = [(
        "Create the fxl package",
        {"fxl/__init__.py": '"""FX-L / FX-XL product fixture."""\n',
         **{f"fxl/{name}.py": graded_module(name, 0) for name in names}})]
    for number in range(2, commits + 1):
        name = names[number % 10]
        level = (number // 10) % (MAX_LEVEL + 1)
        steps.append((f"Change {name} ({number})",
                      {f"fxl/{name}.py": graded_module(name, level, number)}))
    return _build(path, steps)


RISK_COMPLEX_FILES = ("risk/parser.py", "risk/rules.py", "risk/engine.py")


def fx_risk_files() -> dict[str, str]:
    """Return FX-RISK's 15 files: 12 simple, clean modules and 3 complex, smelly ones."""
    files = {f"risk/simple_{index:02d}.py": f'"""Simple module {index}."""\n\n\n'
             f'def value_{index}():\n    """Return {index}."""\n    return {index}\n'
             for index in range(12)}
    for rank, path in enumerate(RISK_COMPLEX_FILES):
        branches = "".join(f"    if X == {level}:\n        Y = {level}\n"
                           for level in range(12 + 4 * rank))
        files[path] = f"import os\nimport sys\n\n\ndef Check(X):\n{branches}    return Y\n"
    return files


def build_fx_risk(path: Path) -> BuiltFixture:
    """Build FX-RISK: one commit with 15 files, 3 deliberately complex and smelly."""
    return _build(path, [("Fifteen files", dict(fx_risk_files()))])


def build_fx_up(path: Path) -> BuiltFixture:
    """Build FX-UP: 12 commits, each adding a module of the next level."""
    return _build(path, [(f"Add level {level}", {f"fxup/m{level:02d}.py":
                                                 graded_module(f"m{level:02d}", level)})
                         for level in range(1, MAX_LEVEL + 1)])


def build_fx_down(path: Path) -> BuiltFixture:
    """Build FX-DOWN: all twelve levels, then 11 commits each deleting the highest."""
    steps: list[tuple[str, dict[str, str | bytes | None]]] = [(
        "Add levels 1 to 12", {f"fxdown/m{level:02d}.py": graded_module(f"m{level:02d}", level)
                               for level in range(1, MAX_LEVEL + 1)})]
    steps += [(f"Remove level {level}", {f"fxdown/m{level:02d}.py": None})
              for level in range(MAX_LEVEL, 1, -1)]
    return _build(path, steps)


BUILDERS = {
    "fx-s": build_fx_s, "fx-err": build_fx_err, "fx-m": build_fx_m,
    "fx-l": lambda path: build_fx_history(path, 200),
    "fx-xl": lambda path: build_fx_history(path, 500),
    "fx-100": lambda path: build_fx_count(path, 100),
    "fx-101": lambda path: build_fx_count(path, 101),
    "fx-102": lambda path: build_fx_count(path, 102),
    "fx-risk": build_fx_risk, "fx-up": build_fx_up, "fx-down": build_fx_down,
}


def main(argv: list[str] | None = None) -> int:
    """Build one fixture into the given directory and print its SHAs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fixture", choices=sorted(BUILDERS))
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    args = parser.parse_args(argv)
    for sha in BUILDERS[args.fixture](args.output).shas:
        print(sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
