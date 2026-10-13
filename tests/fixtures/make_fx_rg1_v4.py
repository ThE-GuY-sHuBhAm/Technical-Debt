"""FX-RG1 v4: FX-RG1 v3 unchanged, plus the RE-13 covariate cases (Task 7 Day 8).

SYNTHETIC — not CL-03. v4 builds v3 with make_fx_rg1_v3.build() and appends commits on
top of v3's last commit, so every v1, v2 and v3 commit and SHA is identical and the
earlier builders are untouched (Day 8 decision Q14). Trace statuses come from the
SYNTHETIC classifier by subject line (run_support: AI_SUBJECTS_V4 and BOT_SUBJECTS_V4);
they are test labels, never CL-03 decisions.

Three items are introduced in the RE-13 window, one per package module of pkg/cov/:

* alpha (item A, c = "Extend alpha", Jane, t = 2027-08-31 10:00 UTC). The module was
  added as pkg/cov/old_name.py by an AI-labelled commit and renamed, so its origin is
  found through a rename. Its lineage has commits exactly at t - 6 months (counted, the
  day clamped from 31 to 28 February), one hour before that (not counted), a bot commit
  (counted), one dated exactly t and one dated after t but placed before c (clock skew;
  neither counted). c also changes a text file and adds a binary file.
* beta (item B, c = "Extend beta", Ravi, t = 2027-12-01 00:00 UTC). Added by a
  bot-labelled commit; t + W ends exactly at the cut-off, so later activity is recorded.
  After c, a commit dated after the cut-off (never read) sits before one dated before it.
* gamma (item C, c = "Add gamma", Asha, t = 2028-01-10 08:00 UTC). c adds the file again
  after an earlier draft was added and deleted, so its lineage starts at c; t + W ends
  after the cut-off, so later activity is null.

Later commits by the three authors change docs/notes.txt, outside the package. Every
expected value is hand-calculated in test_re13_covariates.py.

Usage: python make_fx_rg1_v4.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import make_fx_rg1_v3
from fixture_git import FixtureRepo
from make_fx_rg1 import ASHA, DEPENDENCY_BOT, JANE, RAVI

VERSION = "4"
W_MONTHS = 6
CUTOFF = datetime(2028, 6, 1, 0, 0, tzinfo=timezone.utc)
CLONE_TIME = datetime(2028, 6, 3, 0, 0, tzinfo=timezone.utc)
COVARIATES_WINDOW = "{start: 2027-08-01, end: 2028-02-01}"
ALPHA_OLD = "pkg/cov/old_name.py"
ALPHA = "pkg/cov/alpha.py"
BETA = "pkg/cov/beta.py"
GAMMA = "pkg/cov/gamma.py"
NOTES = "docs/notes.txt"
LOGO = "assets/logo.bin"


def alpha(version: int, extra: bool = False) -> str:
    """Return alpha's text: one warning in label(), and a second one in extra() if asked."""
    text = ('"""Alpha module."""\n\nVERSION = ' f"{version}\n\n\n"
            'def label():\n    """Return a label."""\n    return f"alpha"\n')
    if extra:
        text += '\n\ndef extra():\n    """Return an extra label."""\n    return f"extra"\n'
    return text


def beta(value: int, extra: bool = False) -> str:
    """Return beta's text: no warning, and one in text() if asked."""
    text = ('"""Beta module."""\n\n\ndef value():\n    """Return a value."""\n'
            f"    return {value}\n")
    if extra:
        text += '\n\ndef text():\n    """Return a text."""\n    return f"beta"\n'
    return text


GAMMA_DRAFT = '"""Gamma draft."""\n\nDRAFT = True\n'
GAMMA_TEXT = '"""Gamma module."""\n\n\ndef name():\n    """Return a name."""\n    return f"gamma"\n'
LOGO_BYTES = b"\x89LOGO\x00\x01\x02\x03\x00binary\x00"

# (label, subject, files, author, ISO date), in first-parent order after v3's last commit.
STEPS = (
    ("alpha_add", "Add the old name module", {ALPHA_OLD: alpha(0), NOTES: "v0\n"}, JANE,
     "2027-02-28T09:00:00+00:00"),
    ("alpha_lower", "Tune the old name module", {ALPHA_OLD: alpha(1)}, RAVI,
     "2027-02-28T10:00:00+00:00"),
    ("beta_add", "Add generated beta", {BETA: beta(0)}, DEPENDENCY_BOT,
     "2027-03-15T12:00:00+00:00"),
    ("alpha_rename", "Rename the old name module", {ALPHA_OLD: None, ALPHA: alpha(1)}, JANE,
     "2027-04-01T09:00:00+00:00"),
    ("alpha_bot", "Bump alpha pins", {ALPHA: alpha(2)}, DEPENDENCY_BOT,
     "2027-05-01T09:00:00+00:00"),
    ("beta_lower", "Tune beta", {BETA: beta(1)}, RAVI, "2027-06-01T00:00:00+00:00"),
    ("alpha_skew", "Tidy alpha", {ALPHA: alpha(3)}, RAVI, "2027-09-05T09:00:00+00:00"),
    ("alpha_at_t", "Touch alpha at t", {ALPHA: alpha(4)}, RAVI, "2027-08-31T10:00:00+00:00"),
    ("c_alpha", "Extend alpha", {ALPHA: alpha(4, extra=True), NOTES: "v1\n", LOGO: LOGO_BYTES},
     JANE, "2027-08-31T10:00:00+00:00"),
    ("jane_note", "Note the alpha change", {NOTES: "v2\n"}, JANE, "2027-09-10T09:00:00+00:00"),
    ("jane_backdated", "Backdated note", {NOTES: "v3\n"}, JANE, "2027-08-20T09:00:00+00:00"),
    ("gamma_draft", "Add gamma draft", {GAMMA: GAMMA_DRAFT}, ASHA, "2027-09-01T09:00:00+00:00"),
    ("beta_asha", "Adjust beta", {BETA: beta(2)}, ASHA, "2027-10-01T09:00:00+00:00"),
    ("gamma_drop", "Drop gamma draft", {GAMMA: None}, ASHA, "2027-10-20T09:00:00+00:00"),
    ("c_beta", "Extend beta", {BETA: beta(2, extra=True)}, RAVI, "2027-12-01T00:00:00+00:00"),
    ("c_gamma", "Add gamma", {GAMMA: GAMMA_TEXT}, ASHA, "2028-01-10T08:00:00+00:00"),
    ("jane_end_of_w", "Leap-day note", {NOTES: "v4\n"}, JANE, "2028-02-29T10:00:00+00:00"),
    ("ravi_march", "March note", {NOTES: "v5\n"}, RAVI, "2028-03-01T09:00:00+00:00"),
    ("jane_after_stop", "Backdated February note", {NOTES: "v6\n"}, JANE,
     "2028-02-01T09:00:00+00:00"),
    ("ravi_end_of_w", "Cut-off note", {NOTES: "v7\n"}, RAVI, "2028-06-01T00:00:00+00:00"),
    ("ravi_after_cutoff", "After the cut-off", {NOTES: "v8\n"}, RAVI,
     "2028-06-02T09:00:00+00:00"),
    ("ravi_backdated", "Backdated May note", {NOTES: "v9\n"}, RAVI,
     "2028-05-20T09:00:00+00:00"),
)
FIRST_PARENT_V4 = tuple(step[0] for step in STEPS)


@dataclass(frozen=True)
class FxRg1V4:
    """A built FX-RG1 v4 repository and the SHA of each labelled commit, v1 to v3's too."""

    path: Path
    shas: dict[str, str]
    v3_shas: dict[str, str] = field(default_factory=dict)
    version: str = VERSION


def build(path: Path) -> FxRg1V4:
    """Build FX-RG1 v4 at ``path`` (which must not exist) and return its commit SHAs."""
    v3 = make_fx_rg1_v3.build(path)
    repo = FixtureRepo.existing(path)
    shas = dict(v3.shas)
    for label, subject, files, author, date in STEPS:
        shas[label] = repo.commit(subject, files, author=author, date=date)
    return FxRg1V4(Path(path), shas, dict(v3.shas))


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG1 v4 into the given directory and print each labelled SHA."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, sha in fixture.shas.items():
        print(f"{sha} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
