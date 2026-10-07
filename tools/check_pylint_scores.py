"""NFR-12 gate: every project .py file scores at least 8.0 in Pylint's default configuration.

TC-134 runs Pylint over all project files together and reads the score per file. This
script does the same in one Pylint run, so cross-file checks such as R0801
duplicate-code take part, and derives each file's score from Pylint's default
evaluation formula, that file's statement count and the messages raised in it.

* Configuration. Pylint gets an empty rcfile, so no pylintrc, setup.cfg or
  pyproject.toml section in the tree or the home directory can change the result. The
  script stops if Pylint's evaluation expression is not the default one.
* Attribution. Pylint raises R0801 once, at the end of the run, against whichever
  module it checked last. The message names every module involved, so it counts
  against each of those files instead.
* Empty files. A file with no statements scores 10.0 when it has no messages; with
  messages its statement count is taken as one, so a syntax error scores 0.
* Files. Every .py file under the root, skipping directories whose names start with a
  dot, virtual environments (a pyvenv.cfg), __pycache__, build, dist, node_modules and
  *.egg-info. Nothing else is excluded.

Exit status: 0 every file meets the minimum; 1 at least one file is below it;
2 the check could not run (no files, a changed configuration, a file Pylint did not
analyse, or a message Pylint could not tie to a file).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pylint.lint import Run
from pylint.message import Message
from pylint.reporters import CollectingReporter
from pylint.utils import LinterStats

DEFAULT_EVALUATION = (
    "max(0, 0 if fatal else 10.0 - ((float(5 * error + warning + refactor + "
    "convention) / statement) * 10))"
)
CATEGORIES = ("fatal", "error", "warning", "refactor", "convention", "info")
SKIPPED_DIRECTORIES = frozenset({"__pycache__", "build", "dist", "node_modules"})
_DUPLICATE_MODULE = re.compile(r"^==(?P<module>[^:\[\]\n]+):\[", re.MULTILINE)

EXIT_PASS = 0
EXIT_BELOW_MINIMUM = 1
EXIT_CANNOT_CHECK = 2


class CheckError(Exception):
    """The gate cannot give a reliable result."""


@dataclass
class FileScore:
    """Pylint's statement count and message counts for one file."""

    path: Path
    statements: int | None = None
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(CATEGORIES, 0))

    @property
    def score(self) -> float:
        """Return Pylint's default evaluation applied to this file alone."""
        counts = self.counts
        if counts["fatal"]:
            return 0.0
        penalty = 5 * counts["error"] + counts["warning"] + counts["refactor"]
        penalty += counts["convention"]
        if penalty == 0:
            return 10.0
        statements = max(self.statements or 0, 1)
        return max(0.0, 10.0 - penalty / statements * 10)


@dataclass
class GateResult:
    """Everything one Pylint run tells the gate."""

    files: dict[Path, FileScore]
    messages: list[tuple[Message, list[Path]]]

    @property
    def unattributed(self) -> list[Message]:
        """Return the messages that could not be tied to any project file."""
        return [message for message, charged in self.messages if not charged]

    @property
    def unanalysed(self) -> list[Path]:
        """Return the files Pylint never reported a statement count for."""
        return sorted(path for path, item in self.files.items() if item.statements is None)


class _ScoreReporter(CollectingReporter):
    """Collects messages and records each file's statement count as Pylint leaves it.

    Pylint keys its per-module statistics by module name, and two files outside any
    package can share one (two conftest.py files). The count is therefore read at the
    moment Pylint moves on to the next file and stored under the file path.
    """

    name = "tdea-score"

    def __init__(self) -> None:
        """Start with no messages and no counts."""
        super().__init__()
        self.statements: dict[Path, int] = {}
        self.module_files: dict[str, set[Path]] = defaultdict(set)
        self._current: tuple[str, Path] | None = None

    def on_set_current_module(self, module: str, filepath: str | None) -> None:
        """Record the file Pylint is leaving, then remember the one it starts."""
        self._record_current()
        if filepath is None:
            self._current = None
            return
        path = Path(filepath).resolve()
        self.module_files[module].add(path)
        self._current = (module, path)

    def on_close(self, stats: LinterStats, previous_stats: LinterStats | None) -> None:
        """Record the last file once Pylint has finished."""
        self._record_current()

    def _record_current(self) -> None:
        """Store the statement count of the file Pylint is currently on."""
        if self._current is None:
            return
        module, path = self._current
        module_stats = self.linter.stats.by_module.get(module)
        if module_stats is not None:
            self.statements[path] = module_stats["statement"]


def _skip_directory(path: Path) -> bool:
    """Return True for directories that hold no project source."""
    name = path.name
    return (
        name.startswith(".")
        or name in SKIPPED_DIRECTORIES
        or name.endswith(".egg-info")
        or (path / "pyvenv.cfg").is_file()
    )


def project_files(root: Path) -> list[Path]:
    """Return every project .py file under ``root``, resolved and sorted."""
    root = Path(root).resolve()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not _skip_directory(here / name))
        found.extend(here / name for name in filenames if name.endswith(".py"))
    return sorted(found)


def _charged_files(
    message: Message, reporter: _ScoreReporter, files: dict[Path, FileScore]
) -> list[Path]:
    """Return the files a message counts against; empty when none is a project file."""
    if message.symbol == "duplicate-code":
        paths: set[Path] = set()
        for match in _DUPLICATE_MODULE.finditer(message.msg):
            paths |= reporter.module_files.get(match["module"].strip(), set())
        return sorted(path for path in paths if path in files)
    path = Path(message.abspath).resolve()
    return [path] if path in files else []


def score_files(files: Sequence[Path]) -> GateResult:
    """Run Pylint once over ``files`` with the default configuration and score each file."""
    paths = [Path(path).resolve() for path in files]
    reporter = _ScoreReporter()
    with tempfile.TemporaryDirectory() as tmp:
        rcfile = Path(tmp) / "pylintrc"
        rcfile.write_text("", encoding="utf-8")
        run = Run([f"--rcfile={rcfile}", *map(str, paths)], reporter=reporter, exit=False)
    evaluation = run.linter.config.evaluation
    if evaluation != DEFAULT_EVALUATION:
        raise CheckError(f"Pylint's evaluation expression is not the default: {evaluation}")

    result = GateResult({path: FileScore(path) for path in paths}, [])
    for path, statements in reporter.statements.items():
        if path in result.files:
            result.files[path].statements = statements
    for message in reporter.messages:
        charged = _charged_files(message, reporter, result.files)
        result.messages.append((message, charged))
        for path in charged:
            result.files[path].counts[message.category] += 1
    return result


def _display(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` with forward slashes, for stable output."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _print_report(result: GateResult, root: Path, minimum: float) -> None:
    """Print Pylint's messages, then one score line per file, lowest first.

    A duplicate-code message is shown against the files it is counted against, not
    the unrelated file Pylint attaches it to.
    """
    for message, charged in result.messages:
        if message.symbol == "duplicate-code":
            where = ", ".join(_display(path, root) for path in charged) or "(no project file)"
            print(f"{where}: {message.msg_id} ({message.symbol}) {message.msg}")
            continue
        where = _display(Path(message.abspath).resolve(), root)
        print(f"{where}:{message.line}:{message.column}: {message.msg_id} "
              f"({message.symbol}) {message.msg}")
    if result.messages:
        print()
    print(f"Per-file Pylint scores, default configuration (minimum {minimum:.2f}):")
    ordered = sorted(result.files.values(), key=lambda item: (item.score, str(item.path)))
    for item in ordered:
        mark = "FAIL" if item.score < minimum else "ok"
        statements = "-" if item.statements is None else str(item.statements)
        print(f"  {item.score:5.2f}  {mark:4}  {statements:>5} statements  "
              f"{_display(item.path, root)}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate and return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="project root to search for .py files (default: current directory)")
    parser.add_argument("--min-score", type=float, default=8.0,
                        help="lowest score a file may have (default: 8.0, NFR-12)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    files = project_files(root)
    if not files:
        print(f"No .py files found under {root}.", file=sys.stderr)
        return EXIT_CANNOT_CHECK
    try:
        result = score_files(files)
    except CheckError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CANNOT_CHECK

    _print_report(result, root, args.min_score)
    failing = [item for item in result.files.values() if item.score < args.min_score]
    print(f"\n{len(files)} files checked; {len(failing)} below {args.min_score:.2f}.")
    if result.unanalysed or result.unattributed:
        for path in result.unanalysed:
            print(f"Pylint did not analyse {_display(path, root)}.", file=sys.stderr)
        for message in result.unattributed:
            print(f"Message not tied to a project file: {message.msg_id} {message.msg}",
                  file=sys.stderr)
        return EXIT_CANNOT_CHECK
    return EXIT_BELOW_MINIMUM if failing else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
