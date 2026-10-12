"""FX-RG1 v2: FX-RG1 v1 unchanged, plus the RE-10 and RE-08 cases (Task 7 Day 6 decision 6).

v2 builds v1 with make_fx_rg1.build() and appends commits on top of v1's last commit, so
every v1 commit and SHA is identical and v1's builder is untouched. It adds:

* RE-10 (FR-55), four items, each an f-string without interpolation (W1309) in its own
  package module: A is introduced, its file is renamed with an edit, and a later commit
  fixes it (c1 after the rename); B persists past the end of W = 6 months and is fixed
  only after that; C is introduced less than 6 months before the cut-off and fixed only
  after it; D is fixed by a commit whose committer date lies 7 days before D's
  introduction (clock skew).
* RE-08 (FR-53, FR-54), three commits that each add a warning on an added line: a commit
  touching only tests/, a dependency-bot commit, and a merge commit whose first-parent
  diff adds a module with a warning.

Identities are v1's (ASHA, RAVI, JANE, DEPENDENCY_BOT); no new identity is introduced.
Whether the tests-only and bot commits introduce anything is CL-03's question: this
fixture only provides the cases, and resolves nothing about CL-03.

The run files of the RE-10 tests use W = 6 and the cut-off CUTOFF, with B censored
"window" (end of W 2026-10-09 09:00, before the cut-off) and C censored "cut-off" (end of
W 2027-01-13, after it). All dates lie before October 2026's clone time.

Usage: python make_fx_rg1_v2.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1
from fixture_git import FixtureRepo
from make_fx_rg1 import ASHA, DEPENDENCY_BOT, JANE, RAVI

VERSION = "2"
W_MONTHS = 6
CUTOFF = datetime(2026, 10, 11, 0, 0, tzinfo=timezone.utc)
CLONE_TIME = datetime(2026, 10, 12, 0, 0, tzinfo=timezone.utc)
SYMBOL = "f-string-without-interpolation"

FIRST_PARENT_V2 = (
    "a_intro", "b_intro", "tests_only_warning", "a_rename", "bot_warning",
    "main_parallel_v2", "merge_warning", "a_fix", "b_touch", "d_intro", "d_fix",
    "c_intro", "b_fix", "c_fix",
)
SIDE_BRANCH_V2 = ("feature_flags",)


def module(item: str, function: str, value: str, docstring: str | None = None,
           extra: str = "") -> str:
    """Return a package module whose one function returns an f-string without interpolation."""
    title = docstring or f"Item {item} of RE-10."
    return (f'"""{title}"""\n\n\ndef {function}():\n    """Return a value."""\n'
            f'    return f"{value}"\n{extra}')


def fixed(text: str, value: str) -> str:
    """Return ``text`` with the item's f-string replaced by a plain string."""
    return text.replace(f'return f"{value}"', f'return "{value}"')


A_TEXT = module("A", "greet", "hello")
A_RENAMED = A_TEXT.replace("Item A of RE-10.", "Item A of RE-10, renamed.")
B_TEXT = module("B", "label", "label")
B_TOUCHED = B_TEXT + '\n\ndef other():\n    """Return another value."""\n    return 2\n'
C_TEXT = module("C", "colour", "blue")
D_TEXT = module("D", "door", "open")
TESTS_TEXT = ('"""Tests for the tracked modules."""\n\n\ndef test_label():\n'
              '    """Check the label."""\n    value = f"label"\n    assert value\n')
BOT_TEXT = module("bot", "pinned", "2.32.3", docstring="Pinned versions, written by a bot.")
FLAGS_TEXT = module("merge", "flags", "on", docstring="Feature flags from the side branch.")

# The item lines (Pylint numbering) and their files, for the RE-10 checks.
ITEM_LINE = 6
ITEMS = {
    "A": ("pkg/tracked_a.py", "greet", 'return f"hello"'),
    "B": ("pkg/tracked_b.py", "label", 'return f"label"'),
    "C": ("pkg/tracked_c.py", "colour", 'return f"blue"'),
    "D": ("pkg/tracked_d.py", "door", 'return f"open"'),
}


@dataclass(frozen=True)
class FxRg1V2:
    """A built FX-RG1 v2 repository and the SHA of each labelled commit, v1's included."""

    path: Path
    shas: dict[str, str]
    v1_shas: dict[str, str] = field(default_factory=dict)
    version: str = VERSION

    def first_parent_shas(self) -> list[str]:
        """Return the first-parent history from root to HEAD."""
        return ([self.shas[label] for label in make_fx_rg1.FIRST_PARENT]
                + [self.shas[label] for label in FIRST_PARENT_V2])


def build(path: Path) -> FxRg1V2:
    """Build FX-RG1 v2 at ``path`` (which must not exist) and return its commit SHAs."""
    v1 = make_fx_rg1.build(path)
    repo = FixtureRepo.existing(path)
    shas = dict(v1.shas)
    shas["a_intro"] = repo.commit("Add the greeting module", {"pkg/tracked_a.py": A_TEXT},
                                  author=ASHA, date="2026-04-08T09:00:00+00:00")
    shas["b_intro"] = repo.commit("Add the label module", {"pkg/tracked_b.py": B_TEXT},
                                  author=RAVI, date="2026-04-09T09:00:00+00:00")
    shas["tests_only_warning"] = repo.commit(
        "Test the label", {"tests/test_tracked.py": TESTS_TEXT},
        author=RAVI, date="2026-04-13T09:00:00+00:00")
    repo.git("mv", "pkg/tracked_a.py", "pkg/tracked_alpha.py")
    shas["a_rename"] = repo.commit("Rename the greeting module",
                                   {"pkg/tracked_alpha.py": A_RENAMED},
                                   author=ASHA, date="2026-04-20T09:00:00+00:00")
    shas["bot_warning"] = repo.commit("Pin versions", {"pkg/bot_pinned.py": BOT_TEXT},
                                      author=DEPENDENCY_BOT, date="2026-04-27T06:00:00+00:00")
    _merge_with_a_warning(repo, shas)
    shas["a_fix"] = repo.commit("Use a plain greeting",
                                {"pkg/tracked_alpha.py": fixed(A_RENAMED, "hello")},
                                author=JANE, date="2026-05-11T15:30:27+00:00")
    shas["b_touch"] = repo.commit("Add another value", {"pkg/tracked_b.py": B_TOUCHED},
                                  author=RAVI, date="2026-06-01T09:00:00+00:00")
    shas["d_intro"] = repo.commit("Add the door module", {"pkg/tracked_d.py": D_TEXT},
                                  author=ASHA, date="2026-07-06T10:00:00+00:00")
    shas["d_fix"] = repo.commit("Use a plain door state",
                                {"pkg/tracked_d.py": fixed(D_TEXT, "open")},
                                author=RAVI, date="2026-06-29T10:00:00+00:00")
    shas["c_intro"] = repo.commit("Add the colour module", {"pkg/tracked_c.py": C_TEXT},
                                  author=JANE, date="2026-07-13T09:00:00+00:00")
    shas["b_fix"] = repo.commit("Use a plain label",
                                {"pkg/tracked_b.py": fixed(B_TOUCHED, "label")},
                                author=ASHA, date="2026-10-10T09:00:00+00:00")
    shas["c_fix"] = repo.commit("Use a plain colour",
                                {"pkg/tracked_c.py": fixed(C_TEXT, "blue")},
                                author=JANE, date="2026-10-11T12:00:00+00:00")
    return FxRg1V2(Path(path), shas, dict(v1.shas))


def _merge_with_a_warning(repo: FixtureRepo, shas: dict[str, str]) -> None:
    """Add a side-branch module with a warning, a parallel main commit and the merge."""
    repo.git("checkout", "-q", "-b", "flags")
    shas["feature_flags"] = repo.commit("Add feature flags", {"pkg/feature_flags.py": FLAGS_TEXT},
                                        author=RAVI, date="2026-05-02T10:00:00+00:00")
    repo.git("checkout", "-q", "main")
    shas["main_parallel_v2"] = repo.commit("Describe the tracked modules", {
        "README.md": "# FX-RG1\n\nDeterministic research fixture for TDEA.\n\n"
                     "Built by tests/fixtures/make_fx_rg1.py and make_fx_rg1_v2.py.\n",
    }, author=ASHA, date="2026-05-04T09:00:00+00:00")
    shas["merge_warning"] = repo.merge("flags", "Merge branch 'flags'",
                                       author=ASHA, date="2026-05-06T09:00:00+00:00")
    repo.git("branch", "-q", "-D", "flags")


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG1 v2 into the given directory and print each labelled SHA."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, sha in fixture.shas.items():
        print(f"{sha} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
