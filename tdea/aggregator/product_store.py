"""ProductStore: tdea.db, the SQLite product store (FR-19 to FR-21, NFR-08; sprint 23 Oct).

The file holds the four product entities alone (product_models); opening a file with
any other table is refused, so it never mixes with research.db or lintcache.db. Foreign
keys are enforced. Results survive restarts because they live in the file (FR-20).

Transactions (NFR-08): the repository row is written when an analysis starts (status
Running) and every commit, file metric and aggregate of a finished analysis is written
in one transaction together with the status Complete, replacing the previous results of
that repository. An analysis that is interrupted therefore leaves no commit records at
all; recover_interrupted() marks a repository still Running from a process that no
longer exists as Error when the store is opened again.

Queries (FR-21) are SQLAlchemy expressions with bound parameters, filtered by
repository_id, commit hash, file path and metric type; no value is ever formatted into
SQL text. METRICS lists the metric types a caller may ask for.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, null, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from tdea.aggregator.product_models import (COMPLETE, ERROR, RUNNING, AggregateMetric, Commit,
                                            FileMetric, ProductBase, Repository)
from tdea.aggregator.research_models import canonical_json, iso_utc
from tdea.aggregator.research_store import open_sqlite
from tdea.aggregator.scoring import CommitAggregate

DEFAULT_PRODUCT_STORE = Path("~/.tdea/tdea.db")
FILE_METRICS = ("loc", "sloc", "comments", "blank", "function_count", "mean_cc", "max_cc",
                "mi", "convention", "refactor", "warning", "error", "smells",
                "smell_density")
AGGREGATE_METRICS = ("file_count", "total_loc", "total_sloc", "mean_cc", "mean_mi",
                     "convention", "refactor", "warning", "error", "total_smells",
                     "mean_smell_density", "debt_score", "debt_delta")
METRICS = tuple(sorted(set(FILE_METRICS) | set(AGGREGATE_METRICS)))


class UnknownMetric(ValueError):
    """A metric type outside METRICS was asked for (FR-21)."""


@dataclass(frozen=True)
class StoredFile:
    """One file's metrics, ready for the store."""

    path: str
    loc: int
    sloc: int
    comments: int
    blank: int
    functions: tuple[tuple[str, int, int, str], ...]  # name, line, complexity, rank
    mean_cc: float
    max_cc: int
    cc_rank: str
    mi: int
    convention: int
    refactor: int
    warning: int
    error: int
    smell_density: float


@dataclass(frozen=True)
class StoredCommit:
    """One analysed commit with its files and scored aggregate."""

    sha: str
    sequence: int
    message: str
    author_name: str
    author_email: str
    authored_at: str
    changed_files: tuple[tuple[str, str], ...]
    file_errors: int
    files: tuple[StoredFile, ...]
    aggregate: CommitAggregate


class ProductStore:
    """tdea.db: the four product entities and their transactions."""

    def __init__(self, path: Path | str = DEFAULT_PRODUCT_STORE) -> None:
        """Open or create the product store at ``path``."""
        self._path = Path(path).expanduser()
        self._engine: Engine = open_sqlite(self._path, ProductBase.metadata, "product store")

    @property
    def path(self) -> Path:
        """Return the store's file path."""
        return self._path

    def close(self) -> None:
        """Release the file."""
        self._engine.dispose()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Yield a session whose work is committed together, or not at all."""
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            yield session

    @contextmanager
    def reader(self) -> Iterator[Session]:
        """Yield a session for reading."""
        with Session(self._engine, expire_on_commit=False) as session:
            yield session

    # --- writes ---------------------------------------------------------------------

    def start(self, full_name_key: str, url: str, owner: str, name: str, sampling_n: int,
              now: datetime) -> int:
        """Record that an analysis of the repository starts; return its repository_id."""
        with self.transaction() as session:
            row = session.scalars(select(Repository).where(
                Repository.full_name_key == full_name_key)).one_or_none()
            if row is None:
                row = Repository(full_name_key=full_name_key, url=url, owner=owner,
                                 name=name, commits_analysed=0, file_errors=0)
                session.add(row)
            row.url, row.owner, row.name = url, owner, name
            row.status, row.sampling_n = RUNNING, sampling_n
            row.requested_at = iso_utc(now)
            row.error_type = row.error_cause = row.error_action = None
            session.flush()
            return row.repository_id

    def fail(self, repository_id: int, error_type: str, cause: str, action: str) -> None:
        """Mark the analysis failed with its three-part error; earlier results are kept."""
        with self.transaction() as session:
            row = session.get(Repository, repository_id)
            row.status = ERROR
            row.error_type, row.error_cause, row.error_action = error_type, cause, action

    def complete(self, repository_id: int, commits_total: int,
                 commits: Sequence[StoredCommit], now: datetime) -> None:
        """Replace the repository's results with ``commits`` in one transaction (NFR-08)."""
        with self.transaction() as session:
            old = select(Commit.commit_id).where(Commit.repository_id == repository_id)
            session.execute(delete(FileMetric).where(FileMetric.commit_id.in_(old)))
            session.execute(delete(AggregateMetric).where(AggregateMetric.commit_id.in_(old)))
            session.execute(delete(Commit).where(Commit.repository_id == repository_id))
            for item in commits:
                session.add(_commit_row(repository_id, item))
            session.flush()
            ids = dict(session.execute(select(Commit.sequence, Commit.commit_id).where(
                Commit.repository_id == repository_id)).tuples().all())
            for item in commits:
                commit_id = ids[item.sequence]
                session.add_all(_file_row(commit_id, stored) for stored in item.files)
                session.add(_aggregate_row(commit_id, item.aggregate))
            row = session.get(Repository, repository_id)
            row.status, row.commits_total = COMPLETE, commits_total
            row.commits_analysed = len(commits)
            row.file_errors = sum(item.file_errors for item in commits)
            row.last_completed_at = iso_utc(now)

    def recover_interrupted(self) -> int:
        """Mark analyses left Running by a stopped process as Error; return how many."""
        with self.transaction() as session:
            rows = session.scalars(select(Repository).where(Repository.status == RUNNING)).all()
            for row in rows:
                row.status = ERROR
                row.error_type = "Analysis interrupted"
                row.error_cause = "TDEA stopped before the analysis finished."
                row.error_action = "Submit the repository again to analyse it."
            return len(rows)

    # --- queries (FR-21) ------------------------------------------------------------

    def repository(self, repository_id: int) -> Repository | None:
        """Return one repository row."""
        with self.reader() as session:
            return session.get(Repository, repository_id)

    def find(self, full_name_key: str) -> Repository | None:
        """Return the repository row for an owner/name key (FR-22)."""
        with self.reader() as session:
            return session.scalars(select(Repository).where(
                Repository.full_name_key == full_name_key)).one_or_none()

    def repositories(self) -> list[Repository]:
        """Return every repository, most recently requested first."""
        with self.reader() as session:
            return list(session.scalars(select(Repository).order_by(
                Repository.requested_at.desc(), Repository.repository_id.desc())))

    def commits(self, repository_id: int) -> list[tuple[Commit, AggregateMetric]]:
        """Return a repository's commits with their aggregates, by sequence."""
        with self.reader() as session:
            return list(session.execute(
                select(Commit, AggregateMetric)
                .join(AggregateMetric, AggregateMetric.commit_id == Commit.commit_id)
                .where(Commit.repository_id == repository_id)
                .order_by(Commit.sequence)).tuples())

    def commit(self, repository_id: int, sha: str) -> Commit | None:
        """Return one commit of a repository by full or abbreviated hash (>= 7 chars)."""
        if len(sha) < 7:
            return None
        with self.reader() as session:
            found = list(session.scalars(select(Commit).where(
                Commit.repository_id == repository_id,
                Commit.sha.startswith(sha.lower(), autoescape=True))).all())
            return found[0] if len(found) == 1 else None

    def files(self, repository_id: int, sha: str | None = None,
              path: str | None = None) -> list[tuple[Commit, FileMetric]]:
        """Return file metrics of a repository, filtered by commit hash and file path."""
        query = (select(Commit, FileMetric)
                 .join(FileMetric, FileMetric.commit_id == Commit.commit_id)
                 .where(Commit.repository_id == repository_id))
        if sha is not None:
            query = query.where(Commit.sha == sha)
        if path is not None:
            query = query.where(FileMetric.path == path)
        with self.reader() as session:
            return list(session.execute(query.order_by(Commit.sequence,
                                                       FileMetric.path)).tuples())

    def metric(self, repository_id: int, metric: str, sha: str | None = None,
               path: str | None = None) -> list[tuple[int, str, str | None, float]]:
        """Return (sequence, commit hash, file path or None, value) of one metric type.

        A file metric gives one row per file (filtered by commit hash and file path); an
        aggregate metric one row per commit (filtered by commit hash). A name that is
        both, such as mean_cc, is read from the files when a path is given.
        """
        if metric not in METRICS:
            raise UnknownMetric(f"unknown metric type {metric!r}")
        if metric in FILE_METRICS and (path is not None or metric not in AGGREGATE_METRICS):
            column = getattr(FileMetric, metric)
            query = (select(Commit.sequence, Commit.sha, FileMetric.path, column)
                     .join(FileMetric, FileMetric.commit_id == Commit.commit_id))
            if path is not None:
                query = query.where(FileMetric.path == path)
            order = (Commit.sequence, FileMetric.path)
        else:
            column = getattr(AggregateMetric, metric)
            query = (select(Commit.sequence, Commit.sha, null(), column)
                     .join(AggregateMetric, AggregateMetric.commit_id == Commit.commit_id))
            order = (Commit.sequence,)
        query = query.where(Commit.repository_id == repository_id)
        if sha is not None:
            query = query.where(Commit.sha == sha)
        with self.reader() as session:
            return [tuple(row) for row in session.execute(query.order_by(*order))]


def _commit_row(repository_id: int, item: StoredCommit) -> Commit:
    """Return the commits row of one analysed commit."""
    return Commit(repository_id=repository_id, sha=item.sha, sequence=item.sequence,
                  message=item.message, author_name=item.author_name,
                  author_email=item.author_email, authored_at=item.authored_at,
                  changed_files=canonical_json([list(pair) for pair in item.changed_files]),
                  file_errors=item.file_errors)


def _file_row(commit_id: int, item: StoredFile) -> FileMetric:
    """Return the file_metrics row of one file."""
    functions = [{"name": name, "line": line, "complexity": complexity, "rank": rank}
                 for name, line, complexity, rank in item.functions]
    return FileMetric(commit_id=commit_id, path=item.path, loc=item.loc, sloc=item.sloc,
                      comments=item.comments, blank=item.blank,
                      function_count=len(item.functions), mean_cc=item.mean_cc,
                      max_cc=item.max_cc, cc_rank=item.cc_rank,
                      functions=json.dumps(functions, separators=(",", ":")), mi=item.mi,
                      convention=item.convention, refactor=item.refactor,
                      warning=item.warning, error=item.error,
                      smells=item.convention + item.refactor + item.warning + item.error,
                      smell_density=item.smell_density)


def _aggregate_row(commit_id: int, item: CommitAggregate) -> AggregateMetric:
    """Return the aggregate_metrics row of one commit."""
    return AggregateMetric(
        commit_id=commit_id, file_count=item.file_count, total_loc=item.total_loc,
        total_sloc=item.total_sloc, mean_cc=item.mean_cc, mean_mi=item.mean_mi,
        convention=item.convention, refactor=item.refactor, warning=item.warning,
        error=item.error, total_smells=item.total_smells,
        mean_smell_density=item.mean_smell_density, sloc_growth=item.sloc_growth,
        sloc_norm=item.sloc_norm, cc_norm=item.cc_norm, mi_norm=item.mi_norm,
        smells_norm=item.smells_norm, density_norm=item.density_norm,
        sloc_growth_norm=item.sloc_growth_norm, debt_score=item.debt_score,
        debt_delta=item.debt_delta, classification=item.classification)
