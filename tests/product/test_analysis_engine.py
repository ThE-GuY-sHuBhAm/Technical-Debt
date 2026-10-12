"""FR-16: one file's analysis failure never stops the rest, and is logged safely.

The analysis functions here raise the failures FR-16 names (UnicodeDecodeError,
SyntaxError, an empty file, a non-Python file) on inert byte strings; nothing is run.
"""

from __future__ import annotations

import ast
import logging

import pytest

from tdea.analyzer.analysis_engine import FileFailure, isolate

SHA = "d" * 40
FILES = [
    ("ok.py", b"VALUE = 1\n"),
    ("latin1.py", b"NAME = 'caf\xe9'\n"),
    ("broken.py", b"def broken(:\n"),
    ("empty.py", b""),
    ("notes.txt", b"plain text"),
    ("also_ok.py", b"OTHER = 2\n"),
]


def analyse(path: str, content: bytes) -> int:
    """Count top-level statements; fail like a real analyzer on bad input."""
    if not path.endswith(".py"):
        raise ValueError("not a Python file")
    if not content:
        raise ValueError("empty file")
    return len(ast.parse(content.decode("utf-8")).body)


def test_failures_are_isolated_and_the_rest_analysed() -> None:
    """Good files keep their results in order; each failure is recorded with its type."""
    outcome = isolate(SHA, FILES, analyse)
    assert outcome.results == (("ok.py", 1), ("also_ok.py", 1))
    assert outcome.failures == (
        FileFailure("latin1.py", SHA, "UnicodeDecodeError"),
        FileFailure("broken.py", SHA, "SyntaxError"),
        FileFailure("empty.py", SHA, "ValueError"),
        FileFailure("notes.txt", SHA, "ValueError"),
    )


def test_each_failure_is_logged_with_path_commit_and_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log has path, commit hash and exception type, never the exception message."""
    caplog.set_level(logging.WARNING, logger="tdea.analyzer.analysis_engine")
    isolate(SHA, FILES, analyse)
    assert len(caplog.records) == 4
    assert "path=broken.py" in caplog.text and f"commit={SHA}" in caplog.text
    assert "exception=SyntaxError" in caplog.text
    assert "invalid syntax" not in caplog.text and "caf" not in caplog.text


def test_interrupts_are_not_swallowed() -> None:
    """KeyboardInterrupt still stops the analysis."""
    def interrupted(_path: str, _content: bytes) -> int:
        """Simulate the user stopping the run."""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        isolate(SHA, FILES, interrupted)
        