"""Per-file product metrics: radon raw, cc and mi (FR-11 to FR-13), Pylint counts (FR-14).

Radon reads the decoded text and never imports or runs it. measure() returns, for one
file version: LOC, SLOC, comment and blank lines (radon raw), the cyclomatic complexity
and rank (A to F) of every function and method (radon cc; methods are named
``Class.method``, nested functions are included), and the Maintainability Index as an
integer 0 to 100 (radon mi with multi-line strings as comments, rounded half to even and
clamped). The source is decoded by PEP 263 rules (radon_analyzer.decode_source).

FR-16 failures raise, for analysis_engine.isolate() to log and skip: an undecodable file
(UnicodeDecodeError or SyntaxError), a file that does not parse (SyntaxError), and an
empty file (EmptyFile, sprint 23 Oct reading: FR-16 lists empty files among the failures
to isolate, and a zero-byte module has no metrics to report).

PylintCounter runs Pylint once per commit on the files it is given, in the checked-out
tree, and counts each file's messages by category: C (convention), R (refactor),
W (warning) and E (error) (FR-14). Info and fatal messages are not smells and are not
counted. Pylint runs as ``sys.executable -I -m pylint`` with an explicit empty rcfile,
no plugins, PYTHONPATH and PYLINTRC removed and PYLINTHOME in a temporary folder, so a
pylintrc, setup.cfg or pyproject.toml inside the analysed repository is never read and
no repository code is imported or run. The empty rcfile gives Pylint's default
configuration. A Pylint process that fails or times out raises PylintFailed, which the
analyzer records as a failure of each file it was linting.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from radon.complexity import cc_rank, cc_visit
from radon.metrics import mi_visit
from radon.raw import analyze
from radon.visitors import Class, Function

from tdea.analyzer.radon_analyzer import decode_source

CATEGORIES = {"convention": "C", "refactor": "R", "warning": "W", "error": "E"}
PYLINT_TIME_LIMIT = 600.0


class EmptyFile(ValueError):
    """A zero-byte Python file: nothing to measure (FR-16)."""


class PylintFailed(RuntimeError):
    """Pylint did not produce a usable result for a batch of files."""


@dataclass(frozen=True)
class FunctionComplexity:
    """One function or method's cyclomatic complexity and rank (FR-12)."""

    name: str
    line: int
    complexity: int
    rank: str


@dataclass(frozen=True)
class RadonResult:
    """radon raw, cc and mi for one file version."""

    loc: int
    sloc: int
    comments: int
    blank: int
    functions: tuple[FunctionComplexity, ...]
    mi: int


@dataclass(frozen=True)
class SmellCounts:
    """One file's Pylint messages by category (FR-14)."""

    convention: int = 0
    refactor: int = 0
    warning: int = 0
    error: int = 0

    @property
    def total(self) -> int:
        """Return the number of counted messages."""
        return self.convention + self.refactor + self.warning + self.error


@dataclass(frozen=True)
class FileMetrics:
    """Everything FR-11 to FR-15 record for one file at one commit."""

    path: str
    radon: RadonResult
    smells: SmellCounts

    @property
    def mean_cc(self) -> float:
        """Return the mean complexity of the file's functions, 0.0 when it has none."""
        values = [function.complexity for function in self.radon.functions]
        return sum(values) / len(values) if values else 0.0

    @property
    def max_cc(self) -> int:
        """Return the highest function complexity, 0 when the file has none."""
        return max((function.complexity for function in self.radon.functions), default=0)

    @property
    def cc_rank(self) -> str:
        """Return the radon rank of the file's mean complexity (A for no functions)."""
        return cc_rank(self.mean_cc)

    @property
    def smell_density(self) -> float:
        """Return Pylint messages ÷ SLOC, 0.0 when SLOC is 0 (FR-15)."""
        return self.smells.total / self.radon.sloc if self.radon.sloc else 0.0


def measure(content: bytes) -> RadonResult:
    """Return radon's raw, cc and mi results for one file version, or raise (FR-16)."""
    if not content:
        raise EmptyFile("empty file")
    text = decode_source(content)
    raw = analyze(text)
    functions = []
    for block in cc_visit(text):
        functions.extend(_functions(block))
    functions.sort(key=lambda item: (item.line, item.name))
    mi = min(100, max(0, round(mi_visit(text, True))))
    return RadonResult(raw.loc, raw.sloc, raw.comments, raw.blank, tuple(functions), mi)


def _functions(block: object, prefix: str = "") -> list[FunctionComplexity]:
    """Return a radon block's functions, closures and inner-class methods.

    cc_visit lists top-level functions, classes and the methods of top-level classes;
    closures and the methods of inner classes are only inside their parents.
    """
    found: list[FunctionComplexity] = []
    if isinstance(block, Class):
        for inner in block.inner_classes:
            inner_prefix = f"{prefix}{block.name}.{inner.name}."
            for method in inner.methods:
                found.extend(_functions(method, inner_prefix))
            found.extend(_functions(inner, f"{prefix}{block.name}."))
    elif isinstance(block, Function):
        owner = f"{block.classname}." if block.is_method and not prefix else prefix
        name = f"{owner}{block.name}"
        found.append(FunctionComplexity(name, block.lineno, block.complexity,
                                        cc_rank(block.complexity)))
        for closure in block.closures:
            found.extend(_functions(closure, f"{name}."))
    return found


class PylintCounter:
    """Counts Pylint messages per file and category, one isolated process per batch."""

    def __init__(self, time_limit: float = PYLINT_TIME_LIMIT) -> None:
        """Stop a batch that runs longer than ``time_limit`` seconds."""
        self._time_limit = time_limit

    def count(self, root: Path, paths: Sequence[str]) -> dict[str, SmellCounts]:
        """Return the counts of each path (relative POSIX paths under ``root``)."""
        if not paths:
            return {}
        with tempfile.TemporaryDirectory(prefix="tdea-pylint-") as temporary:
            rcfile = Path(temporary) / "empty.pylintrc"
            rcfile.write_bytes(b"")
            environment = {name: value for name, value in os.environ.items()
                           if name.upper() not in ("PYTHONPATH", "PYLINTRC")}
            environment["PYLINTHOME"] = str(Path(temporary) / "home")
            command = [sys.executable, "-I", "-m", "pylint", f"--rcfile={rcfile}",
                       "--load-plugins=", "--output-format=json", "--persistent=n",
                       "--jobs=1", "--recursive=n", "--", *paths]
            try:
                completed = subprocess.run(command, cwd=root, env=environment,
                                           capture_output=True, timeout=self._time_limit,
                                           check=False)
            except subprocess.TimeoutExpired as exc:
                raise PylintFailed("Pylint did not finish within the time limit") from exc
        if completed.returncode & 32 or completed.returncode < 0:
            raise PylintFailed("Pylint stopped with a usage error or crashed")
        try:
            messages = json.loads(completed.stdout.decode("utf-8") or "[]")
        except ValueError as exc:
            raise PylintFailed("Pylint's output could not be read") from exc
        totals = {path: dict.fromkeys(CATEGORIES, 0) for path in paths}
        for message in messages:
            path = str(message.get("path", "")).replace("\\", "/")
            category = message.get("type")
            if path in totals and category in CATEGORIES:
                totals[path][category] += 1
        return {path: SmellCounts(**counts) for path, counts in totals.items()}
