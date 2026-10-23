"""ProductAnalyzer: the product's static-analysis pipeline over one clone (Modules 2-3).

For each selected commit, oldest first: check the working tree out at the commit
(FR-09), list its regular .py files (CON-01; other files are skipped without error),
measure each with radon (FR-11 to FR-13) and count its Pylint messages (FR-14), with
every file-level failure isolated, logged and counted (FR-16, analysis_engine.isolate).
Smell density follows (FR-15, FileMetrics). A progress callback receives an integer
percentage after every commit, so the frontend gets an update at least every 10
commits (FR-18).

Reuse within one analysis (sprint 23 Oct, for the NFR-01 time bound): a file version's
radon result depends only on its bytes, so it is computed once per blob SHA. Its Pylint
counts are computed in the tree of the first analysed commit that holds that version and
reused while the same version stays at the same path. Pylint can see other modules when
it infers types, so a count reused at a later commit reflects the tree it was first
linted in; the counts are still the same on every run of the same repository and
sampling (NFR-06). Nothing is cached between analyses.

The analyzer reads code only as text (radon) or through the isolated Pylint process
(product_metrics); it never imports or runs repository code.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tdea.analyzer.analysis_engine import FileFailure, isolate
from tdea.analyzer.product_metrics import (FileMetrics, PylintCounter, PylintFailed,
                                           RadonResult, SmellCounts, measure)
from tdea.git_handler.commit_info import CommitInfo
from tdea.git_handler.local_repository import LocalRepository

LOGGER = logging.getLogger(__name__)

Progress = Callable[[int, int, int, str], None]  # percent, processed, total, current SHA


@dataclass(frozen=True)
class CommitAnalysis:
    """One analysed commit: its FR-07 record, file metrics and isolated failures."""

    commit: CommitInfo
    sequence: int
    files: tuple[FileMetrics, ...]
    failures: tuple[FileFailure, ...]


def _no_progress(_percent: int, _processed: int, _total: int, _sha: str) -> None:
    """Ignore progress."""


class ProductAnalyzer:
    """Runs the per-commit product pipeline over a cloned repository."""

    def __init__(self, pylint: PylintCounter | None = None,
                 logger: logging.Logger = LOGGER) -> None:
        """Use ``pylint`` for FR-14 and ``logger`` for FR-16 failure lines."""
        self._pylint = pylint if pylint is not None else PylintCounter()
        self._logger = logger

    def analyse(self, repository: LocalRepository, commits: Sequence[CommitInfo],
                progress: Progress = _no_progress) -> list[CommitAnalysis]:
        """Analyse ``commits`` (oldest first) in the clone and return one result each."""
        radon_cache: dict[str, RadonResult] = {}
        smell_cache: dict[tuple[str, str], SmellCounts] = {}
        results = []
        total = len(commits)
        for number, commit in enumerate(commits, start=1):
            repository.checkout(commit.sha)
            results.append(self._commit(repository, commit, number, radon_cache,
                                        smell_cache))
            progress(number * 100 // total, number, total, commit.sha)
        return results

    def _commit(self, repository: LocalRepository, commit: CommitInfo, sequence: int,
                radon_cache: dict[str, RadonResult],
                smell_cache: dict[tuple[str, str], SmellCounts]) -> CommitAnalysis:
        """Measure one checked-out commit."""
        root = repository.path
        assert root is not None
        files = repository.python_files()
        blobs = dict(files)

        def radon(path: str, blob: str) -> RadonResult:
            if blob not in radon_cache:
                radon_cache[blob] = measure((root / path).read_bytes())
            return radon_cache[blob]

        measured = isolate(commit.sha, files, radon, self._logger)
        failures = list(measured.failures)
        to_lint = [path for path, _result in measured.results
                   if (path, blobs[path]) not in smell_cache]
        try:
            counted = self._pylint.count(root, to_lint)
        except PylintFailed:
            counted = None
            for path in to_lint:
                failures.append(FileFailure(path, commit.sha, "PylintFailed"))
                self._logger.warning("File-level analysis failure: path=%s commit=%s "
                                     "exception=%s", path, commit.sha, "PylintFailed")
        if counted is not None:
            for path, counts in counted.items():
                smell_cache[(path, blobs[path])] = counts
        metrics = tuple(FileMetrics(path, result, smell_cache[(path, blobs[path])])
                        for path, result in measured.results
                        if (path, blobs[path]) in smell_cache)
        failures.sort(key=lambda failure: failure.path)
        return CommitAnalysis(commit, sequence, metrics, tuple(failures))
