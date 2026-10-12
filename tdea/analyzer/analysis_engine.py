"""File-level failure isolation (FR-16), shared by the product and research analyzers.

FR-16: a file-level analysis failure (UnicodeDecodeError, SyntaxError, an empty or a
non-Python file, or any other error in one file) must not stop the analysis of the
remaining files, and each failure is logged with the file path, the commit hash and the
exception type. isolate() applies one analysis function to each file of a commit and
does exactly that. The log line holds only the path, the SHA and the exception type,
never the exception message, which can quote file content (Decision 2: logs hold SHAs,
paths and counts only). KeyboardInterrupt and SystemExit are not caught.

The product AnalysisEngine (Modules 2-3) is built on a later day; this module holds only
the shared helper for now.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar

LOGGER = logging.getLogger(__name__)

Item = TypeVar("Item")
Result = TypeVar("Result")


@dataclass(frozen=True)
class FileFailure:
    """One isolated file-level failure: where it happened and the exception type."""

    path: str
    commit_sha: str
    exception_type: str


@dataclass(frozen=True)
class Isolated(Generic[Result]):
    """The results of the files that succeeded, in input order, and the failures."""

    results: tuple[tuple[str, Result], ...]
    failures: tuple[FileFailure, ...]


def isolate(commit_sha: str, files: Iterable[tuple[str, Item]],
            analyse: Callable[[str, Item], Result],
            logger: logging.Logger = LOGGER) -> Isolated[Result]:
    """Analyse each (path, item) of one commit; a failing file is logged and skipped."""
    results: list[tuple[str, Result]] = []
    failures: list[FileFailure] = []
    for path, item in files:
        try:
            results.append((path, analyse(path, item)))
        except Exception as exc:  # pylint: disable=broad-exception-caught  # FR-16
            failure = FileFailure(path, commit_sha, type(exc).__name__)
            failures.append(failure)
            logger.warning("File-level analysis failure: path=%s commit=%s exception=%s",
                           failure.path, failure.commit_sha, failure.exception_type)
    return Isolated(tuple(results), tuple(failures))
