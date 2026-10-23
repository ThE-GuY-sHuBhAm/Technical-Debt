"""AnalysisService: product analyses in the background (FR-17, FR-18, FR-22, FR-23, CON-03).

submit() handles one URL submission, in this order:

1. FR-02: the URL is parsed; a bad URL or sampling interval is refused at once.
2. FR-22: a repository already analysed (status Complete) is returned from the cache,
   with no pipeline stage run again, unless the caller asks for a fresh analysis.
3. CON-03 / OI-04: the single-analysis lock (shared with research runs) is taken; when
   another analysis holds it, the submission is rejected at once with a clear message.
4. FR-03 / FR-04: the accessibility check, with the optional token for that request
   only. The token goes no further: it is not passed to the background thread, stored,
   logged or put in any message (NFR-19).
5. FR-17: the analysis runs in a daemon thread, so the web interface stays responsive;
   the thread releases the lock when it ends, however it ends.

The thread clones, samples and analyses (ProductEngine), aggregates and scores each
commit (scoring) and stores everything in one transaction (ProductStore.complete,
NFR-08). Progress (FR-18) is kept in memory per repository: an integer percentage after
every commit and the commit being processed. Every run appends one audit line to
``audit.log`` beside tdea.db (FR-23): URL, start and completion times, commits requested
and processed, per-file errors and the outcome; never a token.

A failure inside the run is stored as the repository's three-part error (status Error);
earlier results of that repository are kept, since nothing is replaced unless the new
run completes.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tdea.aggregator.lock import AnalysisLock, LockUnavailable
from tdea.aggregator.product_models import COMPLETE
from tdea.aggregator.product_store import ProductStore, StoredCommit, StoredFile
from tdea.aggregator.research_models import iso_utc
from tdea.aggregator.scoring import FileSample, aggregate, score
from tdea.analyzer.product_engine import ProductEngine, ProductError, ProductRun, Repo

LOGGER = logging.getLogger(__name__)
AUDIT_FILE = "audit.log"


class SubmissionRefused(Exception):
    """A submission refused before any analysis started, in three parts (FR-05)."""

    def __init__(self, kind: str, error_type: str, cause: str, action: str) -> None:
        """Keep the kind and the three parts."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.kind = kind
        self.error_type = error_type
        self.cause = cause
        self.action = action

    @classmethod
    def wrap(cls, error: ProductError | LockUnavailable, kind: str | None = None
             ) -> SubmissionRefused:
        """Return the refusal for a structured lower-layer error."""
        return cls(kind or getattr(error, "kind", "busy"), error.error_type, error.cause,
                   error.action)


@dataclass(frozen=True)
class Submission:
    """The answer to one submission: the repository and whether it came from the cache."""

    repository_id: int
    cached: bool


@dataclass
class Progress:
    """The latest progress of one repository's running analysis (FR-18)."""

    percent: int = 0
    processed: int = 0
    total: int = 0
    current_sha: str | None = None
    stage: str = "cloning"


@dataclass
class _State:
    """The service's in-memory state, guarded by one lock."""

    guard: threading.Lock = field(default_factory=threading.Lock)
    progress: dict[int, Progress] = field(default_factory=dict)
    threads: dict[int, threading.Thread] = field(default_factory=dict)


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


class AnalysisService:
    """Starts product analyses and answers what the api layer asks about them."""

    def __init__(self, store: ProductStore, engine: ProductEngine | None = None,
                 lock_directory: Path | None = None,
                 clock: Callable[[], datetime] = _utc_now) -> None:
        """Use ``store`` (tdea.db) and ``engine``; the lock folder is shared with research."""
        self._store = store
        self._engine = engine if engine is not None else ProductEngine()
        self._lock_directory = lock_directory
        self._clock = clock
        self._state = _State()
        store.recover_interrupted()

    @property
    def store(self) -> ProductStore:
        """Return the product store."""
        return self._store

    def submit(self, url: object, token: str | None = None, sampling_n: object = 2,
               refresh: bool = False) -> Submission:
        """Start an analysis of ``url``, or return the cached one (see the module text)."""
        return self._submit([url], token, sampling_n, refresh)[0]

    def submit_pair(self, first_url: object, second_url: object, token: str | None = None,
                    sampling_n: object = 2, refresh: bool = False
                    ) -> tuple[Submission, Submission]:
        """Analyse two repositories in sequence for the comparison view (FR-44).

        Both URLs are validated and both accessibility checks run before anything
        starts, so the token is used during this request only; the two analyses then
        run one after the other in one background thread under one lock (CON-03). A
        repository already analysed is taken from the cache (FR-22).
        """
        first, second = self._submit([first_url, second_url], token, sampling_n, refresh)
        return first, second

    def _submit(self, urls: list[object], token: str | None, sampling_n: object,
                refresh: bool) -> list[Submission]:
        """Validate, use the cache, take the lock, check and start the analyses."""
        try:
            repos = [self._engine.parse_url(url) for url in urls]
            interval = self._engine.check_sampling(sampling_n)
        except ProductError as exc:
            raise SubmissionRefused.wrap(exc) from None
        if len({repo.key for repo in repos}) != len(repos):
            raise SubmissionRefused("same_repository", "Same repository twice",
                                    "Both URLs name the same repository.",
                                    "Enter two different repositories to compare.")
        answers: list[Submission | None] = []
        pending = []
        for repo in repos:
            cached = self._store.find(repo.key)
            if cached is not None and cached.status == COMPLETE and not refresh:
                answers.append(Submission(cached.repository_id, True))
            else:
                answers.append(None)
                pending.append(repo)
        if not pending:
            return [answer for answer in answers if answer is not None]
        lock = AnalysisLock(self._lock_directory)
        try:
            lock.acquire()
        except LockUnavailable as exc:
            raise SubmissionRefused.wrap(exc, "busy") from None
        try:
            for repo in pending:
                self._engine.check(repo, token)
            jobs = [(self._store.start(repo.key, repo.url, repo.owner, repo.name, interval,
                                       self._clock()), repo) for repo in pending]
        except ProductError as exc:
            lock.release()
            raise SubmissionRefused.wrap(exc) from None
        except BaseException:
            lock.release()
            raise
        thread = threading.Thread(target=self._work, args=(jobs, interval, lock),
                                  name=f"tdea-analysis-{jobs[0][0]}", daemon=True)
        with self._state.guard:
            for repository_id, _repo in jobs:
                self._state.progress[repository_id] = Progress(stage="queued")
                self._state.threads[repository_id] = thread
        thread.start()
        started = iter(jobs)
        return [answer if answer is not None else Submission(next(started)[0], False)
                for answer in answers]

    def progress(self, repository_id: int) -> Progress | None:
        """Return a copy of the running analysis's progress, or None when none runs."""
        with self._state.guard:
            found = self._state.progress.get(repository_id)
            return None if found is None else Progress(**vars(found))

    def wait(self, repository_id: int, timeout: float | None = None) -> bool:
        """Wait for the repository's background analysis; True when it has finished."""
        with self._state.guard:
            thread = self._state.threads.get(repository_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _work(self, jobs: list[tuple[int, Repo]], sampling_n: int,
              lock: AnalysisLock) -> None:
        """Run the analyses one after the other, then release the lock."""
        try:
            for repository_id, repo in jobs:
                self._run(repository_id, repo, sampling_n)
        finally:
            lock.release()

    def _run(self, repository_id: int, repo: Repo, sampling_n: int) -> None:
        """Run one analysis in the background thread and record its outcome."""
        with self._state.guard:
            self._state.progress[repository_id] = Progress()
        started = self._clock()
        audit = {"url": repo.url, "started_at": iso_utc(started), "commits_requested": None,
                 "commits_processed": 0, "file_errors": 0}
        try:
            run = self._engine.run(repo, sampling_n, self._progress_callback(repository_id))
            commits = stored_commits(run)
            audit.update(commits_requested=len(run.analyses), commits_processed=len(commits),
                         file_errors=sum(item.file_errors for item in commits))
            self._store.complete(repository_id, run.commits_total, commits, self._clock())
            audit["outcome"] = COMPLETE
        except ProductError as exc:
            self._store.fail(repository_id, exc.error_type, exc.cause, exc.action)
            audit["outcome"] = f"Error: {exc.error_type}"
        except Exception as exc:  # pylint: disable=broad-exception-caught  # never raw
            LOGGER.error("Analysis failed: repository_id=%s exception=%s", repository_id,
                         type(exc).__name__)
            self._store.fail(repository_id, "Analysis failed",
                             "An unexpected problem stopped the analysis.",
                             "Try the analysis again; if it fails again, report the "
                             "repository URL to the TDEA team.")
            audit["outcome"] = "Error: Analysis failed"
        finally:
            audit["completed_at"] = iso_utc(self._clock())
            self._audit(audit)
            with self._state.guard:
                self._state.progress.pop(repository_id, None)

    def _progress_callback(self, repository_id: int
                           ) -> Callable[[int, int, int, str], None]:
        """Return the callback that records FR-18 progress for one repository."""
        def update(percent: int, processed: int, total: int, sha: str) -> None:
            with self._state.guard:
                state = self._state.progress.setdefault(repository_id, Progress())
                state.percent, state.processed, state.total = percent, processed, total
                state.current_sha, state.stage = sha, "analysing"
        return update

    def _audit(self, entry: dict[str, object]) -> None:
        """Append one FR-23 audit line beside tdea.db."""
        path = self._store.path.parent / AUDIT_FILE
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            LOGGER.error("Audit entry could not be written: %s", path)


def stored_commits(run: ProductRun) -> list[StoredCommit]:
    """Aggregate and score every analysed commit; return the rows to store."""
    samples = [[_sample(item) for item in analysis.files] for analysis in run.analyses]
    aggregates = score([aggregate(analysis.sequence, files)
                        for analysis, files in zip(run.analyses, samples)])
    commits = []
    for analysis, scored in zip(run.analyses, aggregates):
        info = analysis.commit
        commits.append(StoredCommit(
            sha=info.sha, sequence=analysis.sequence, message=info.message,
            author_name=info.author_name, author_email=info.author_email,
            authored_at=info.authored_at,
            changed_files=tuple((item.path, item.change_type.value)
                                for item in info.changed_files),
            file_errors=len(analysis.failures),
            files=tuple(_stored_file(item) for item in analysis.files), aggregate=scored))
    return commits


def _sample(metrics) -> FileSample:
    """Return the scoring view of one FileMetrics."""
    return FileSample(metrics.path, metrics.radon.sloc, metrics.radon.loc,
                      tuple(function.complexity for function in metrics.radon.functions),
                      metrics.mean_cc, metrics.radon.mi, metrics.smells.convention,
                      metrics.smells.refactor, metrics.smells.warning, metrics.smells.error,
                      metrics.smell_density)


def _stored_file(metrics) -> StoredFile:
    """Return the store's view of one FileMetrics."""
    radon = metrics.radon
    return StoredFile(
        path=metrics.path, loc=radon.loc, sloc=radon.sloc, comments=radon.comments,
        blank=radon.blank,
        functions=tuple((item.name, item.line, item.complexity, item.rank)
                        for item in radon.functions),
        mean_cc=round(metrics.mean_cc, 6), max_cc=metrics.max_cc, cc_rank=metrics.cc_rank,
        mi=radon.mi, convention=metrics.smells.convention, refactor=metrics.smells.refactor,
        warning=metrics.smells.warning, error=metrics.smells.error,
        smell_density=round(metrics.smell_density, 6))
