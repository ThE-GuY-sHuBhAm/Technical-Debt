"""CovariateExtractor, started: the D-8 and D-9 covariates as pure functions (FR-57).

Task 7 Day 7 decision (option A): only D-8 and D-9 are built today, as functions that
store nothing; the rest of FR-57 (D-6, D-7, file age, month, author_id and the
exploratory fields), the covariate records and RE-13 follow on Day 8. The readings below
are Day 7's P5, approved provisionally.

* D-8 baseline density: "research-profile messages in the parent version ÷ its SLOC
  (radon, as in FR-15); null for a new file". SLOC is radon's (RadonAnalyzer, PEP 263
  decoding); a parent version with SLOC 0 has density 0.0, as FR-15 gives "Files with
  SLOC = 0 ... a smell density of 0.0".
* D-9 file size: "physical lines at c", counted as Python counts physical lines: CR LF,
  LF and CR each end one, and a last line without an ending is a line.
* D-9 commit size: "lines added + removed, and files changed, in c", from c's FR-50 file
  changes against its first parent; a binary file is a changed file with no changed lines.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tdea.analyzer.radon_analyzer import RadonAnalyzer
from tdea.analyzer.research.rg1.matching import source_lines
from tdea.git_handler.file_changes import FileChange


@dataclass(frozen=True)
class CommitSize:
    """D-9 commit size: changed lines (added + removed) and changed files of c."""

    lines: int
    files: int


def baseline_density(message_count: int, parent: bytes | None,
                     radon: RadonAnalyzer | None = None) -> float | None:
    """Return D-8: messages in the parent version per radon SLOC; None for a new file."""
    if parent is None:
        return None
    sloc = (radon if radon is not None else RadonAnalyzer()).sloc(parent)
    return 0.0 if sloc == 0 else message_count / sloc


def file_size(content: bytes) -> int:
    """Return D-9 file size: the physical lines of the file at c."""
    return len(source_lines(content))


def commit_size(changes: Sequence[FileChange]) -> CommitSize:
    """Return D-9 commit size from c's first-parent file changes (FR-50)."""
    lines = sum((change.lines_added or 0) + (change.lines_removed or 0) for change in changes)
    return CommitSize(lines, len(changes))
