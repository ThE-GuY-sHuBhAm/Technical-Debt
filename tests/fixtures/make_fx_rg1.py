"""FX-RG1 v1: the deterministic research fixture repository (execution plan, Day 2).

Day 2 cases: fixed identities and dates; a merge commit; commits at +05:30 (one crossing
midnight in UTC); Co-authored-by trailers, one from an AI tool; an AI-tool committer
commit and an AI-tool author commit; a dependency-bot commit; a rename with edits (util.py
lines 10-14 edited, then renamed to helpers.py with 2 lines changed, for RE-03); tests/,
docs/, examples/, vendor/ and migrations/ folders; a generated file; and a Signed-off-by
trailer with an e-mail address in the body. The disappearance-mode cases are added on
Days 6-7 as a new fixture version.

AI and bot identities use only identifiers published in P2's paper (copilot-swe-agent[bot],
noreply@anthropic.com, Cursor Agent; arXiv 2603.28592, Sec. III-A) and the bot the RG1
protocol names (dependabot). Every other address is under the reserved .invalid domain.

Usage: python make_fx_rg1.py OUTPUT_DIR (builds locally; nothing is pushed).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from fixture_git import FixtureRepo, Identity

VERSION = "1"

ASHA = Identity("Asha Rao", "asha.rao@example.invalid")
RAVI = Identity("Ravi Menon", "ravi.menon@example.invalid")
JANE = Identity("Jane Doe", "jane.doe@example.invalid")
AI_COMMITTER = Identity("copilot-swe-agent[bot]", "copilot-swe-agent@example.invalid")
AI_AUTHOR = Identity("Cursor Agent", "cursor-agent@example.invalid")
AI_TRAILER = "Co-authored-by: Claude <noreply@anthropic.com>"
DEPENDENCY_BOT = Identity("dependabot[bot]", "dependabot@example.invalid")

FIRST_PARENT = (
    "root", "tests_only", "docs_examples", "vendor_migrations", "generated",
    "trailers", "ai_committer", "ai_author", "dependency_bot", "util_edit", "rename",
    "main_parallel", "merge", "signed_off", "latest",
)
SIDE_BRANCH = ("feature_1", "feature_2")

UTIL_LINES = [
    '"""Utility helpers for the FX-RG1 fixture package."""',
    "",
    "",
    "def summarise(values):",
    '    """Return simple statistics for a list of numbers."""',
    "    count = len(values)",
    "    total = sum(values)",
    "    smallest = min(values)",
    "    largest = max(values)",
    "    spread = largest - smallest",
    "    average = total / count",
    "    midpoint = (largest + smallest) / 2",
    '    label = "summary"',
    "    rounded = round(average, 2)",
    "    return {",
    '        "count": count,',
    '        "spread": spread,',
    '        "average": rounded,',
    '        "midpoint": midpoint,',
    '        "label": label,',
    "    }",
]
UTIL_EDITED_LINES = {  # 1-based line number -> new text; lines 10-14 (RE-03)
    10: "    spread = float(largest - smallest)",
    11: "    average = total / count if count else 0.0",
    12: "    midpoint = (largest + smallest) / 2.0",
    13: '    label = "summary-v2"',
    14: "    rounded = round(average, 3)",
}
HELPERS_CHANGED_LINES = {  # the 2 lines changed by the rename commit
    1: '"""Helper functions for the FX-RG1 fixture package."""',
    13: '    label = "summary-v3"',
}

CORE_V1 = '''"""Core processing for the FX-RG1 fixture package."""


def process(items):
    """Return the items that are truthy."""
    return [item for item in items if item]
'''
CORE_V2 = CORE_V1 + '''

def process_or_empty(items):
    """Return processed items, or an empty list for None."""
    if items is None:
        return []
    return process(items)
'''
CORE_V3 = CORE_V2.replace(
    '    if items is None:\n',
    '    if items is None or not isinstance(items, list):\n',
)
REPORT_V1 = '''"""Report output for the FX-RG1 fixture package."""


def render(rows):
    """Return the rows as text lines."""
    return "\\n".join(str(row) for row in rows)
'''


@dataclass(frozen=True)
class FxRg1:
    """A built FX-RG1 repository and the SHA of each labelled commit."""

    path: Path
    shas: dict[str, str]
    version: str = VERSION

    def first_parent_shas(self) -> list[str]:
        """Return the first-parent history from root to HEAD."""
        return [self.shas[label] for label in FIRST_PARENT]


def _lines(lines: list[str], changes: dict[int, str]) -> list[str]:
    """Return ``lines`` with the given 1-based lines replaced."""
    return [changes.get(number, text) for number, text in enumerate(lines, start=1)]


def _text(lines: list[str]) -> str:
    """Join lines into file text ending in a newline."""
    return "\n".join(lines) + "\n"


def build(path: Path) -> FxRg1:
    """Build FX-RG1 at ``path`` (which must not exist) and return its commit SHAs."""
    repo = FixtureRepo(path)
    shas: dict[str, str] = {}
    shas["root"] = repo.commit("Create package skeleton", {
        "README.md": "# FX-RG1\n\nDeterministic research fixture for TDEA.\n",
        "requirements.txt": "requests==2.31.0\n",
        "pkg/__init__.py": '"""FX-RG1 fixture package."""\n',
        "pkg/util.py": _text(UTIL_LINES),
    }, author=ASHA, date="2026-01-05T09:00:00+00:00")
    shas["tests_only"] = repo.commit("Add tests for util", {
        "tests/test_util.py": "from pkg.util import summarise\n\n\n"
                              "def test_count():\n"
                              "    assert summarise([1, 2])['count'] == 2\n",
    }, author=RAVI, date="2026-01-12T10:30:00+05:30")
    shas["docs_examples"] = repo.commit("Add documentation config and an example", {
        "docs/conf.py": 'project = "fx-rg1"\n',
        "examples/demo.py": "from pkg.util import summarise\n\nprint(summarise([1, 2, 3]))\n",
    }, author=ASHA, date="2026-01-19T11:00:00+05:30")
    shas["vendor_migrations"] = repo.commit("Vendor a module and add the first migration", {
        "vendor/thirdparty.py": '"""Vendored third-party module."""\n\nimport os\n',
        "migrations/0001_initial.py": '"""Initial migration."""\n\noperations = []\n',
    }, author=ASHA, date="2026-01-26T09:00:00+00:00")
    shas["generated"] = repo.commit("Add the generated schema module", {
        "pkg/_generated_schema.py": "# Code generated by fx-rg1-schema-compiler. DO NOT EDIT.\n"
                                    '"""Generated schema constants."""\n\nSCHEMA_VERSION = 1\n',
    }, author=ASHA, date="2026-02-02T09:00:00+00:00")
    shas["trailers"] = repo.commit(
        "Add core processing\n\nWritten in a pairing session.\n\n"
        f"Co-authored-by: {ASHA.name} <{ASHA.email}>\n{AI_TRAILER}",
        {"pkg/core.py": CORE_V1}, author=RAVI, date="2026-02-09T03:00:00+05:30",
    )
    shas["ai_committer"] = repo.commit(
        "Handle missing input in core", {"pkg/core.py": CORE_V2},
        author=RAVI, date="2026-02-16T09:00:00+00:00",
        committer=AI_COMMITTER, committer_date="2026-02-16T09:05:00+00:00",
    )
    shas["ai_author"] = repo.commit(
        "Add report module", {"pkg/report.py": REPORT_V1},
        author=AI_AUTHOR, date="2026-02-23T14:00:00+05:30",
        committer=ASHA, committer_date="2026-02-23T15:00:00+05:30",
    )
    shas["dependency_bot"] = repo.commit(
        "Bump requests from 2.31.0 to 2.32.3", {"requirements.txt": "requests==2.32.3\n"},
        author=DEPENDENCY_BOT, date="2026-03-02T06:00:00+00:00",
    )
    shas["util_edit"] = repo.commit(
        "Refine summary statistics",
        {"pkg/util.py": _text(_lines(UTIL_LINES, UTIL_EDITED_LINES))},
        author=ASHA, date="2026-03-09T09:00:00+00:00",
    )
    repo.git("mv", "pkg/util.py", "pkg/helpers.py")
    shas["rename"] = repo.commit("Rename util to helpers", {
        "pkg/helpers.py": _text(_lines(_lines(UTIL_LINES, UTIL_EDITED_LINES),
                                       HELPERS_CHANGED_LINES)),
    }, author=ASHA, date="2026-03-16T09:00:00+00:00")
    _side_branch_and_merge(repo, shas)
    shas["signed_off"] = repo.commit(
        "Validate core input\n\nReported by jane.doe@example.invalid on the mailing list.\n\n"
        f"Signed-off-by: {JANE.name} <{JANE.email}>",
        {"pkg/core.py": CORE_V3}, author=JANE, date="2026-03-30T09:00:00+00:00",
    )
    shas["latest"] = repo.commit(
        "Tidy report output", {"pkg/report.py": REPORT_V1.replace(
            "Return the rows as text lines.", "Return the rows as text, one row per line.")},
        author=ASHA, date="2026-04-06T18:30:00+05:30",
    )
    return FxRg1(Path(path), shas)


def _side_branch_and_merge(repo: FixtureRepo, shas: dict[str, str]) -> None:
    """Add a two-commit feature branch, one parallel main commit and the merge."""
    repo.git("checkout", "-q", "-b", "feature")
    shas["feature_1"] = repo.commit("Start the feature module", {
        "pkg/feature.py": '"""Feature module."""\n\nENABLED = False\n',
    }, author=RAVI, date="2026-03-18T10:00:00+05:30")
    shas["feature_2"] = repo.commit("Enable the feature", {
        "pkg/feature.py": '"""Feature module."""\n\nENABLED = True\n',
    }, author=RAVI, date="2026-03-20T10:00:00+05:30")
    repo.git("checkout", "-q", "main")
    shas["main_parallel"] = repo.commit("Describe the fixture", {
        "README.md": "# FX-RG1\n\nDeterministic research fixture for TDEA.\n\n"
                     "Built by tests/fixtures/make_fx_rg1.py.\n",
    }, author=ASHA, date="2026-03-19T09:00:00+00:00")
    shas["merge"] = repo.merge("feature", "Merge branch 'feature'",
                               author=ASHA, date="2026-03-23T09:00:00+00:00")
    repo.git("branch", "-q", "-D", "feature")


def main(argv: list[str] | None = None) -> int:
    """Build FX-RG1 into the given directory and print each labelled SHA."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="directory to create (must not exist)")
    fixture = build(parser.parse_args(argv).output)
    for label, sha in fixture.shas.items():
        print(f"{sha} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())