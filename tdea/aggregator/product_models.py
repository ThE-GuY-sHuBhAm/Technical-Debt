"""The four product entities of tdea.db (FR-19, sprint 23 Oct).

tdea.db is the product store: repositories, commits, file_metrics and
aggregate_metrics, with the keys FR-19 names (repository_id; commit_id with
repository_id FK; metric_id with commit_id FK; agg_id with commit_id FK UNIQUE). It has
its own metadata (ProductBase), so no table or foreign key is shared with research.db or
lintcache.db (FR-52).

Implementation choices, recorded here:

* A repository row is keyed for the result cache (FR-22) by ``full_name_key``, the
  owner/name in lower case, because GitHub names are case-insensitive; ``url`` keeps the
  address as submitted. The row also carries the overview fields FR-30 shows (status,
  commits analysed, last completed analysis) and the three-part error of a failed run.
* Commits carry ``sequence``, their 1-based position among the analysed commits, oldest
  first, which every chart and the comparison use as the x-axis (FR-31, FR-45).
* Per-function cyclomatic complexity (FR-12) is kept with its file as canonical JSON
  (``functions``: name, line, complexity, rank), so the store keeps exactly the four
  entities FR-19 lists.
* Debt scoring (FR-24 to FR-27) is stored on aggregate_metrics with the normalised
  values it was computed from, so exports and reports read it without recomputing.
* Timestamps are ISO 8601 UTC text, as in research.db.
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PENDING = "Pending"
RUNNING = "Running"
COMPLETE = "Complete"
ERROR = "Error"
STATUSES = (PENDING, RUNNING, COMPLETE, ERROR)


class ProductBase(DeclarativeBase):
    """Declarative base whose metadata is tdea.db's alone."""


class Repository(ProductBase):
    """One analysed (or requested) GitHub repository (FR-19, FR-30)."""

    __tablename__ = "repositories"

    repository_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name_key: Mapped[str] = mapped_column(String, unique=True)
    url: Mapped[str] = mapped_column(String)
    owner: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default=PENDING)
    sampling_n: Mapped[int] = mapped_column(Integer, default=2)
    commits_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commits_analysed: Mapped[int] = mapped_column(Integer, default=0)
    file_errors: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[str] = mapped_column(String)
    last_completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_action: Mapped[str | None] = mapped_column(Text, nullable=True)


class Commit(ProductBase):
    """One analysed commit with its FR-07 fields (FR-19)."""

    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha"),
                      UniqueConstraint("repository_id", "sequence"))

    commit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.repository_id"))
    sha: Mapped[str] = mapped_column(String)
    sequence: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    author_name: Mapped[str] = mapped_column(String)
    author_email: Mapped[str] = mapped_column(String)
    authored_at: Mapped[str] = mapped_column(String)
    changed_files: Mapped[str] = mapped_column(Text)  # canonical JSON [[path, change type]]
    file_errors: Mapped[int] = mapped_column(Integer, default=0)


class FileMetric(ProductBase):
    """One Python file's metrics at one commit (FR-11 to FR-15)."""

    __tablename__ = "file_metrics"
    __table_args__ = (UniqueConstraint("commit_id", "path"),)

    metric_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.commit_id"))
    path: Mapped[str] = mapped_column(String)
    loc: Mapped[int] = mapped_column(Integer)
    sloc: Mapped[int] = mapped_column(Integer)
    comments: Mapped[int] = mapped_column(Integer)
    blank: Mapped[int] = mapped_column(Integer)
    function_count: Mapped[int] = mapped_column(Integer)
    mean_cc: Mapped[float] = mapped_column(Float)
    max_cc: Mapped[int] = mapped_column(Integer)
    cc_rank: Mapped[str] = mapped_column(String)
    functions: Mapped[str] = mapped_column(Text)  # canonical JSON, FR-12
    mi: Mapped[int] = mapped_column(Integer)
    convention: Mapped[int] = mapped_column(Integer)
    refactor: Mapped[int] = mapped_column(Integer)
    warning: Mapped[int] = mapped_column(Integer)
    error: Mapped[int] = mapped_column(Integer)
    smells: Mapped[int] = mapped_column(Integer)
    smell_density: Mapped[float] = mapped_column(Float)


class AggregateMetric(ProductBase):
    """Repository-wide metrics and the debt score of one commit (FR-24 to FR-27)."""

    __tablename__ = "aggregate_metrics"

    agg_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.commit_id"), unique=True)
    file_count: Mapped[int] = mapped_column(Integer)
    total_loc: Mapped[int] = mapped_column(Integer)
    total_sloc: Mapped[int] = mapped_column(Integer)
    mean_cc: Mapped[float] = mapped_column(Float)
    mean_mi: Mapped[float] = mapped_column(Float)
    convention: Mapped[int] = mapped_column(Integer)
    refactor: Mapped[int] = mapped_column(Integer)
    warning: Mapped[int] = mapped_column(Integer)
    error: Mapped[int] = mapped_column(Integer)
    total_smells: Mapped[int] = mapped_column(Integer)
    mean_smell_density: Mapped[float] = mapped_column(Float)
    sloc_growth: Mapped[int] = mapped_column(Integer)
    sloc_norm: Mapped[float] = mapped_column(Float)
    cc_norm: Mapped[float] = mapped_column(Float)
    mi_norm: Mapped[float] = mapped_column(Float)
    smells_norm: Mapped[float] = mapped_column(Float)
    density_norm: Mapped[float] = mapped_column(Float)
    sloc_growth_norm: Mapped[float] = mapped_column(Float)
    debt_score: Mapped[float] = mapped_column(Float)
    debt_delta: Mapped[float] = mapped_column(Float)
    classification: Mapped[str] = mapped_column(String)
