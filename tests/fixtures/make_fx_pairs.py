"""FX-PAIRS and the single-file profile fixtures (W-03, execution plan Day 2).

FX-PAIRS holds hand-built before/after commit pairs for item matching and introduction:
the four RE-07 cases (a) a flagged line moved 5 lines, (b) two identical flagged lines in
one function with one removed, (c) one symbol in two functions, (d) a flagged line changed
only in whitespace, and the RE-08 case (e) flagged lines that are added, modified and left
untouched. Every pair edits its own file, in one linear history. The flagged lines are
f-strings without interpolation (W1309), which the research profile keeps enabled.

smell_profile.py (RE-04: C0103, C0301 and W0611 triggers) and scopes.py (RE-05: module
level, Cart.total and outer.inner) are written as single files at test time.

This module only builds the data; matching, introduction and linting are later tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fixture_git import FixtureRepo, Identity

VERSION = "1"
AUTHOR = Identity("Lena Ortiz", "lena.ortiz@example.invalid")

_FILLER = "".join(f"    # step {index}\n" for index in range(1, 6))  # 5 lines, no warnings

PAIRS: dict[str, tuple[str, str, str]] = {  # name -> (description, before, after)
    "a_moved": (
        "RE-07 (a): the flagged line moves down 5 lines",
        'def run():\n    """Run."""\n    print(f"start")\n    return 0\n',
        'def run():\n    """Run."""\n' + _FILLER + '    print(f"start")\n    return 0\n',
    ),
    "b_duplicate": (
        "RE-07 (b): two identical flagged lines in one function, one removed",
        'def tick():\n    """Tick twice."""\n    print(f"tick")\n    print(f"tick")\n',
        'def tick():\n    """Tick twice."""\n    print(f"tick")\n',
    ),
    "c_two_scopes": (
        "RE-07 (c): one symbol in two functions",
        'def first():\n    """First."""\n    print(f"same")\n',
        'def first():\n    """First."""\n    print(f"same")\n\n\n'
        'def second():\n    """Second."""\n    print(f"same")\n',
    ),
    "d_whitespace": (
        "RE-07 (d): the flagged line changes only in whitespace",
        'def label():\n    """Label."""\n    print(f"done",  end="")\n',
        'def label():\n    """Label."""\n    print(f"done", end="")\n',
    ),
    "e_added_modified_untouched": (
        "RE-08: flagged lines added, modified and left untouched",
        'def report():\n    """Report."""\n    print(f"header")\n    print(f"body")\n',
        'def report():\n    """Report."""\n    print(f"header")\n    print(f"body text")\n'
        '    print(f"footer")\n',
    ),
}

SMELL_PROFILE = (
    '"""Profile check module (RE-04): C0103, C0301 and W0611 triggers."""\n\n'
    "import os\n\n\n"
    "def BadlyNamedFunction():\n"
    '    """Has a name that is not snake_case (C0103)."""\n'
    "    return 'this line is deliberately far longer than one hundred characters "
    "so that Pylint reports C0301'\n"
)

SCOPES = (
    '"""Scope check module (RE-05): module level, Cart.total and outer.inner."""\n\n'
    'print(f"module level")\n\n\n'
    "class Cart:\n"
    '    """A cart."""\n\n'
    "    def total(self):\n"
    '        """Return the total."""\n'
    '        print(f"total")\n'
    "        return self\n\n"
    "    def size(self):\n"
    '        """Return the size."""\n'
    "        return len(str(self))\n\n\n"
    "def outer():\n"
    '    """Outer function."""\n\n'
    "    def inner():\n"
    '        """Inner function."""\n'
    '        print(f"inner")\n\n'
    "    return inner\n"
)


@dataclass(frozen=True)
class Pair:
    """One before/after commit pair on one file."""

    name: str
    description: str
    path: str
    before_sha: str
    after_sha: str


def build(path: Path) -> dict[str, Pair]:
    """Build FX-PAIRS at ``path`` (which must not exist) and return its pairs by name."""
    repo = FixtureRepo(path)
    pairs = {}
    for day, (name, (description, before, after)) in enumerate(PAIRS.items(), start=1):
        file_path = f"pairs/{name}.py"
        before_sha = repo.commit(f"{name}: before", {file_path: before},
                                 author=AUTHOR, date=f"2026-05-{day:02d}T09:00:00+00:00")
        after_sha = repo.commit(f"{name}: after", {file_path: after},
                                author=AUTHOR, date=f"2026-05-{day:02d}T10:00:00+00:00")
        pairs[name] = Pair(name, description, file_path, before_sha, after_sha)
    return pairs


def write_smell_profile(directory: Path) -> Path:
    """Write smell_profile.py into ``directory`` and return its path."""
    target = Path(directory) / "smell_profile.py"
    target.write_bytes(SMELL_PROFILE.encode())
    return target


def write_scopes(directory: Path) -> Path:
    """Write scopes.py into ``directory`` and return its path."""
    target = Path(directory) / "scopes.py"
    target.write_bytes(SCOPES.encode())
    return target