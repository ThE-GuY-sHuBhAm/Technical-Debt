"""FX-RG1 v3: FX-RG1 v2 unchanged, plus the RE-11 disappearance-mode cases (Task 7 Day 7).

v3 builds v2 with make_fx_rg1_v2.build() and appends commits on top of v2's last commit,
so every v1 and v2 commit and SHA is identical and both builders are untouched (Day 7
decision). One commit adds a package module per case, each with one warning, and later
commits remove each warning in the way its mode needs (FR-56, RE-11):

* sweep_item: a formatting sweep over 25 .py files (24 formatting fixtures and the item's
  file, re-spaced) changes the flagged line's text, so the item disappears in a sweep (a2).
* bulk_fix: a second sweep over the 24 fixtures also fixes this item for real, in a file
  that is not itself formatting-only ("bulk-and-fix is bulk"; reading R1, I1).
* removed: the file is deleted (b). deleted: the item's function is deleted and the other
  function is not similar (c). rewrite: a same-name rewrite at similarity exactly 0.55
  that touches the line (e at tau 0.5, d at 0.6-0.8). local: a local fix at similarity
  exactly 0.9 (e). broken: the fix leaves a syntax error, so the file fails to lint (f,
  "syntax error"). artifact: an unused variable becomes used without its line being
  touched (f).
* message_fix and config_fix: fixed by a commit whose subject, or whose lint.cfg change,
  the SYNTHETIC bulk double (run_support) lists for a3 and a1. These two cases test
  mechanics only: the real D-4 lists are CL-03's and still undefined.
* module_level: a module-level item fixed in place, in its own window (CL-17: the unit
  fails closed; RE-11 expects a local fix).

Identities are v1's (ASHA, RAVI, JANE). The RE-11 run files use W = 6, the windows below
and the cut-off CUTOFF, with a service clock at CLONE_TIME.

Usage: python make_fx_rg1_v3.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1_v2
from fixture_git import FixtureRepo
from make_fx_rg1 import ASHA, JANE, RAVI

VERSION = "3"
W_MONTHS = 6
CUTOFF = datetime(2027, 6, 1, 0, 0, tzinfo=timezone.utc)
CLONE_TIME = datetime(2027, 6, 2, 0, 0, tzinfo=timezone.utc)
MODES_WINDOW = "{start: 2026-11-01, end: 2026-11-15}"
MODULE_WINDOW = "{start: 2027-01-01, end: 2027-02-01}"
FORMAT_FILES = tuple(f"pkg/fmt/m{index:02d}.py" for index in range(1, 25))
CONFIG = "lint.cfg"
MESSAGE_SUBJECT = "SYNTHETIC bulk message: fix the message module"
SYMBOL = "f-string-without-interpolation"

FIRST_PARENT_V3 = (
    "modes_intro", "sweep", "remove", "delete_function", "rewrite", "local_fix", "break",
    "use_spare", "message_fix", "config_fix", "sweep_back", "module_intro", "module_fix",
)


def function_module(title: str, function: str, body: str) -> str:
    """Return a package module with one documented function."""
    return f'"""{title}"""\n\n\ndef {function}():\n    """Return a value."""\n{body}'


NARROW = "VALUES = [1, 2, 3]\n"
WIDE = "VALUES = [\n    1,\n    2,\n    3]\n"


def format_file(index: int, values: str) -> str:
    """Return formatting fixture ``index`` with its list written narrow or wide."""
    return f'"""Formatting fixture {index:02d}."""\n\n{values}'


SWEEP_ITEM = function_module("Mode a2: a sweep re-spaces the flagged line.", "swept",
                             '    return [ f"swept" ]\n')
BULK_FIX = function_module("Mode a2: a sweep that also fixes the line.", "bulk",
                           '    return f"bulk"\n')
REMOVED = function_module("Mode b: the file is removed.", "gone", '    return f"gone"\n')
DELETED = ('"""Mode c: the function is deleted."""\n\n\ndef doomed():\n'
           '    """Return a value."""\n    return f"doomed"\n\n\n'
           'def keeper(values):\n    """Sum the values."""\n    total = 0\n'
           '    for value in values:\n        total += value\n    return total\n')
DELETED_AFTER = ('"""Mode c: the function is deleted."""\n\n\n'
                 'def keeper(values):\n    """Sum the values."""\n    total = 0\n'
                 '    for value in values:\n        total += value\n    return total\n')
REWRITE_HEAD = ('"""Mode d or e: a same-name rewrite at similarity 0.55."""\n\n\n'
                'def shaped(values):\n    """Return a shaped value."""\n')
REWRITE = REWRITE_HEAD + ('    total = 0\n    for value in values:\n        total += value\n'
                          '    return f"shaped"\n')
REWRITE_AFTER = REWRITE_HEAD + '    total = len(set(values))\n    return "shaped" * total\n'
LOCAL = function_module("Mode e: a local fix at similarity 0.9.", "local",
                        '    return f"local", 1\n')
BROKEN = function_module("Mode f: the fix leaves a syntax error.", "broken",
                         '    return f"broken"\n')
ARTIFACT = ('"""Mode f: a variable becomes used without its line changing."""\n\n\n'
            'def unused():\n    """Return a value."""\n    spare = 1\n    return 2\n')
MESSAGE_FIX = function_module("Mode a3 (SYNTHETIC double): fixed by a listed message.",
                              "message", '    return f"message"\n')
CONFIG_FIX = function_module("Mode a1 (SYNTHETIC double): fixed with a config change.",
                             "config", '    return f"config"\n')
MODULE_LEVEL = '"""Mode CL-17: a module-level item fixed in place."""\n\nLABEL = f"module"\n'

# Each case: its file, the item's symbol and line in the introducing version, and its scope.
CASES = {
    "sweep_item": ("pkg/modes/sweep_item.py", SYMBOL, 6, "swept"),
    "bulk_fix": ("pkg/modes/bulk_fix.py", SYMBOL, 6, "bulk"),
    "removed": ("pkg/modes/removed.py", SYMBOL, 6, "gone"),
    "deleted": ("pkg/modes/deleted.py", SYMBOL, 6, "doomed"),
    "rewrite": ("pkg/modes/rewrite.py", SYMBOL, 9, "shaped"),
    "local": ("pkg/modes/local.py", SYMBOL, 6, "local"),
    "broken": ("pkg/modes/broken.py", SYMBOL, 6, "broken"),
    "artifact": ("pkg/modes/artifact.py", "unused-variable", 6, "unused"),
    "message_fix": ("pkg/modes/message_fix.py", SYMBOL, 6, "message"),
    "config_fix": ("pkg/modes/config_fix.py", SYMBOL, 6, "config"),
    "module_level": ("pkg/modes/module_level.py", SYMBOL, 3, ""),
}


@dataclass(frozen=True)
class FxRg1V3:
    """A built FX-RG1 v3 repository and the SHA of each labelled commit, v1's and v2's too."""

    path: Path
    shas: dict[str, str]
    v2_shas: dict[str, str] = field(default_factory=dict)
    version: str = VERSION


def build(path: Path) -> FxRg1V3:
    """Build FX-RG1 v3 at ``path`` (which must not exist) and return its commit SHAs."""
    v2 = make_fx_rg1_v2.build(path)
    repo = FixtureRepo.existing(path)
    shas = dict(v2.shas)
    files = {CASES[name][0]: text for name, text in (
        ("sweep_item", SWEEP_ITEM), ("bulk_fix", BULK_FIX), ("removed", REMOVED),
        ("deleted", DELETED), ("rewrite", REWRITE), ("local", LOCAL), ("broken", BROKEN),
        ("artifact", ARTIFACT), ("message_fix", MESSAGE_FIX), ("config_fix", CONFIG_FIX))}
    files.update({name: format_file(index, NARROW)
                  for index, name in enumerate(FORMAT_FILES, start=1)})
    files[CONFIG] = "[lint]\nlevel = 1\n"
    shas["modes_intro"] = repo.commit("Add the mode modules", files, author=ASHA,
                                      date="2026-11-02T09:00:00+00:00")
    sweep = {name: format_file(index, WIDE) for index, name in enumerate(FORMAT_FILES, start=1)}
    sweep[CASES["sweep_item"][0]] = SWEEP_ITEM.replace('[ f"swept" ]', '[f"swept"]')
    shas["sweep"] = repo.commit("Reformat the package", sweep, author=RAVI,
                                date="2026-11-20T09:00:00+00:00")
    _fixes(repo, shas)
    back = {name: format_file(index, NARROW) for index, name in enumerate(FORMAT_FILES, start=1)}
    back[CASES["bulk_fix"][0]] = BULK_FIX.replace('f"bulk"', '"bulk"')
    shas["sweep_back"] = repo.commit("Reformat back and fix the bulk value", back,
                                     author=RAVI, date="2026-11-29T09:00:00+00:00")
    module = CASES["module_level"][0]
    shas["module_intro"] = repo.commit("Add the module label", {module: MODULE_LEVEL},
                                       author=JANE, date="2027-01-05T09:00:00+00:00")
    shas["module_fix"] = repo.commit("Use a plain module label",
                                     {module: MODULE_LEVEL.replace('f"module"', '"module"')},
                                     author=ASHA, date="2027-02-10T09:00:00+00:00")
    return FxRg1V3(Path(path), shas, dict(v2.shas))


def _fixes(repo: FixtureRepo, shas: dict[str, str]) -> None:
    """Add the commits that end the b to f cases and the two SYNTHETIC bulk cases."""
    def path(name: str) -> str:
        return CASES[name][0]

    steps = (
        ("remove", "Remove the obsolete module", {path("removed"): None}, JANE, "21"),
        ("delete_function", "Drop the doomed function", {path("deleted"): DELETED_AFTER},
         ASHA, "22"),
        ("rewrite", "Rework the shaping", {path("rewrite"): REWRITE_AFTER}, RAVI, "23"),
        ("local_fix", "Use a plain local value",
         {path("local"): LOCAL.replace('f"local"', '"local"')}, JANE, "24"),
        ("break", "Start the broken edit",
         {path("broken"): BROKEN.replace('return f"broken"', 'return "broken"(')}, ASHA,
         "25"),
        ("use_spare", "Use the spare value",
         {path("artifact"): ARTIFACT.replace("return 2", "return spare")}, RAVI, "26"),
        ("message_fix", MESSAGE_SUBJECT,
         {path("message_fix"): MESSAGE_FIX.replace('f"message"', '"message"')}, JANE, "27"),
        ("config_fix", "Tune the lint settings",
         {CONFIG: "[lint]\nlevel = 2\n",
          path("config_fix"): CONFIG_FIX.replace('f"config"', '"config"')}, ASHA, "28"),
    )
    for label, subject, files, author, day in steps:
        shas[label] = repo.commit(subject, files, author=author,
                                  date=f"2026-11-{day}T09:00:00+00:00")


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG1 v3 into the given directory and print each labelled SHA."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, sha in fixture.shas.items():
        print(f"{sha} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
