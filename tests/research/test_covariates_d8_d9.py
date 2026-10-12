"""D-8 and D-9 covariates as pure functions (FR-57, started on Day 7; reading P5).

Mechanics on hand-made inputs: nothing is stored on Day 7, and FR-57's records and RE-13
follow on Day 8 (Day 7 decision, option A).
"""

from __future__ import annotations

from tdea.analyzer.radon_analyzer import RadonAnalyzer
from tdea.analyzer.research.rg1.covariates import (CommitSize, baseline_density, commit_size,
                                                   file_size)
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk

PARENT = b'"""Doc."""\n\nX = 1\nY = 2\n\n\ndef f():\n    return X + Y\n'


def test_baseline_density_is_messages_per_radon_sloc() -> None:
    """D-8: messages in the parent version / its SLOC (4 code lines)."""
    assert RadonAnalyzer().sloc(PARENT) == 4
    assert baseline_density(2, PARENT) == 0.5


def test_baseline_density_is_null_for_a_new_file_and_0_for_zero_sloc() -> None:
    """Null (not 0.0) when c adds the file; 0.0 when the parent has no code lines."""
    assert baseline_density(3, None) is None
    assert baseline_density(0, b'"""Only a docstring."""\n') == 0.0
    assert baseline_density(5, b"") == 0.0


def test_file_size_counts_physical_lines_for_every_line_ending() -> None:
    """D-9: CR LF, LF and CR each end a physical line; an unterminated last line counts."""
    assert file_size(b"") == 0
    assert file_size(b"a = 1\n") == 1
    assert file_size(b"a = 1") == 1
    assert file_size(b"a = 1\r\nb = 2\rc = 3\n") == 3
    assert file_size(b"a = 1\n\n\n") == 3


def change(path: str, hunks: tuple[Hunk, ...], binary: bool = False) -> FileChange:
    """Return a hand-made modification of ``path``."""
    return FileChange(ChangeType.MODIFIED, FileSide(path, "o"), FileSide(path, "n"), None,
                      binary, hunks)


def test_commit_size_counts_hunk_lines_and_files_with_binary_files_as_zero_lines() -> None:
    """D-9: added + removed lines from FR-50 hunks; a binary file adds a file, no lines."""
    changes = [change("a.py", (Hunk(1, 2, 1, 3),)), change("b.txt", (Hunk(5, 0, 6, 1),)),
               change("logo.png", (), binary=True)]
    assert commit_size(changes) == CommitSize(lines=6, files=3)
    assert commit_size([]) == CommitSize(0, 0)
