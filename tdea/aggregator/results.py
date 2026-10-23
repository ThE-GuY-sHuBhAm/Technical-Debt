"""Read models for the dashboard, exports, reports and comparison (FR-28 to FR-47).

Everything here reads tdea.db through ProductStore and returns plain data (dicts, lists
and numbers), so the api layer can serve it as JSON, CSV or PDF without running any
pipeline stage again (FR-42). Nothing is written.

* overview(): the FR-30 card (name, owner, URL, commits analysed, status, time of the
  most recent completed analysis) plus the stored three-part error, if any.
* series(): one point per analysed commit, oldest first, with the fields every trend
  chart and tooltip needs (FR-31 to FR-35, NFR-10).
* commit_files(): the FR-37 drill-down of one commit.
* high_risk(): the FR-28 / FR-36 table; patterns(): FR-29; statistics(): FR-40 (d).
* csv_rows(): FR-41's per-commit dataset, columns in the order FR-41 lists them.
* comparison(): FR-45 to FR-47 for two repositories aligned by 1-based commit sequence;
  comparison_rows(): FR-46's CSV rows, one per sequence number up to the longer
  repository, blank cells for the shorter one (OI-06 proposal).
"""

from __future__ import annotations

import json
from dataclasses import asdict

from tdea.aggregator.product_models import AggregateMetric, Commit, Repository
from tdea.aggregator.product_store import ProductStore
from tdea.aggregator.scoring import (CommitAggregate, FileSample, high_risk_files,
                                     patterns as find_patterns, statistics as describe,
                                     summary)

CSV_COLUMNS = ("commit_hash", "commit_sequence", "timestamp", "author", "SLOC", "mean_CC",
               "mean_MI", "total_smells", "smell_density", "debt_score", "debt_delta",
               "debt_classification")
COMPARISON_COLUMNS = ("commit_sequence", "repo_1_debt_score", "repo_1_delta",
                      "repo_1_classification", "repo_2_debt_score", "repo_2_delta",
                      "repo_2_classification")
STATISTIC_METRICS = (("SLOC", "total_sloc"), ("Mean cyclomatic complexity", "mean_cc"),
                     ("Mean Maintainability Index", "mean_mi"),
                     ("Total code smells", "total_smells"),
                     ("Mean smell density", "mean_smell_density"),
                     ("Debt Score", "debt_score"))


class NotFound(LookupError):
    """The repository, commit or result asked for does not exist."""


class NotReady(LookupError):
    """The repository has no completed analysis to show yet."""


def overview(store: ProductStore, repository_id: int) -> dict[str, object]:
    """Return the FR-30 overview of one repository."""
    row = _repository(store, repository_id)
    error = None
    if row.error_type:
        error = {"type": row.error_type, "cause": row.error_cause, "action": row.error_action}
    return {"repository_id": row.repository_id, "name": row.name, "owner": row.owner,
            "url": row.url, "status": row.status, "commits_analysed": row.commits_analysed,
            "commits_total": row.commits_total, "sampling_n": row.sampling_n,
            "file_errors": row.file_errors, "last_completed_at": row.last_completed_at,
            "requested_at": row.requested_at, "error": error}


def repositories(store: ProductStore) -> list[dict[str, object]]:
    """Return the overview of every repository in the store (NFR-16)."""
    return [overview(store, row.repository_id) for row in store.repositories()]


def series(store: ProductStore, repository_id: int) -> list[dict[str, object]]:
    """Return one point per analysed commit, oldest first (FR-31 to FR-35)."""
    _completed(store, repository_id)
    return [_point(commit, agg) for commit, agg in store.commits(repository_id)]


def commit_files(store: ProductStore, repository_id: int, sha: str) -> dict[str, object]:
    """Return the FR-37 per-file breakdown of one commit."""
    _completed(store, repository_id)
    commit = store.commit(repository_id, sha)
    if commit is None:
        raise NotFound(f"commit {sha} is not an analysed commit of this repository")
    files = []
    for _commit, item in store.files(repository_id, sha=commit.sha):
        files.append({"path": item.path, "loc": item.loc, "sloc": item.sloc,
                      "comments": item.comments, "blank": item.blank,
                      "mean_cc": item.mean_cc, "max_cc": item.max_cc,
                      "cc_rank": item.cc_rank, "mi": item.mi,
                      "convention": item.convention, "refactor": item.refactor,
                      "warning": item.warning, "error": item.error, "smells": item.smells,
                      "smell_density": item.smell_density,
                      "functions": json.loads(item.functions)})
    return {"sha": commit.sha, "sequence": commit.sequence, "message": commit.message,
            "author": commit.author_name, "timestamp": commit.authored_at,
            "file_errors": commit.file_errors,
            "changed_files": [{"path": path, "change_type": kind}
                              for path, kind in json.loads(commit.changed_files)],
            "files": files}


def high_risk(store: ProductStore, repository_id: int) -> list[dict[str, object]]:
    """Return the ten highest-risk files (FR-28, FR-36)."""
    _completed(store, repository_id)
    samples = [FileSample(item.path, item.sloc, item.loc, (), item.mean_cc, item.mi,
                          item.convention, item.refactor, item.warning, item.error,
                          item.smell_density)
               for _commit, item in store.files(repository_id)]
    return [asdict(row) for row in high_risk_files(samples)]


def patterns(store: ProductStore, repository_id: int) -> list[dict[str, object]]:
    """Return the FR-29 debt patterns with their commit ranges."""
    points = series(store, repository_id)
    found = find_patterns([(point["sequence"], point["classification"]) for point in points])
    by_sequence = {point["sequence"]: point["sha"] for point in points}
    return [dict(asdict(item), first_sha=by_sequence[item.first_sequence],
                 last_sha=by_sequence[item.last_sequence]) for item in found]


def statistics(store: ProductStore, repository_id: int) -> list[dict[str, object]]:
    """Return mean, minimum, maximum and standard deviation of each metric (FR-40 d)."""
    rows = store.commits(repository_id)
    table = []
    for label, column in STATISTIC_METRICS:
        values = [float(getattr(agg, column)) for _commit, agg in rows]
        average, low, high, deviation = describe(values)
        table.append({"metric": label, "mean": average, "min": low, "max": high,
                      "std": deviation})
    return table


def csv_rows(store: ProductStore, repository_id: int) -> list[tuple[object, ...]]:
    """Return FR-41's per-commit rows, in CSV_COLUMNS order."""
    return [(point["sha"], point["sequence"], point["timestamp"], point["author"],
             point["sloc"], point["mean_cc"], point["mean_mi"], point["total_smells"],
             point["smell_density"], point["debt_score"], point["debt_delta"],
             point["classification"]) for point in series(store, repository_id)]


def comparison(store: ProductStore, first: int, second: int) -> dict[str, object]:
    """Return both repositories' DS timelines and FR-47 summaries."""
    sides = []
    for repository_id in (first, second):
        points = series(store, repository_id)
        aggregates = [_aggregate(point) for point in points]
        sides.append({"overview": overview(store, repository_id),
                      "timeline": [{"sequence": point["sequence"], "sha": point["sha"],
                                    "debt_score": point["debt_score"],
                                    "debt_delta": point["debt_delta"],
                                    "classification": point["classification"]}
                                   for point in points],
                      "summary": asdict(summary(aggregates))})
    return {"repositories": sides}


def comparison_rows(store: ProductStore, first: int, second: int
                    ) -> list[tuple[object, ...]]:
    """Return FR-46's rows: one per sequence number up to the longer repository."""
    left, right = series(store, first), series(store, second)
    rows = []
    for index in range(max(len(left), len(right))):
        row: list[object] = [index + 1]
        for side in (left, right):
            if index < len(side):
                point = side[index]
                row += [point["debt_score"], point["debt_delta"], point["classification"]]
            else:
                row += [None, None, None]
        rows.append(tuple(row))
    return rows


def _repository(store: ProductStore, repository_id: int) -> Repository:
    """Return the repository row or raise NotFound."""
    row = store.repository(repository_id)
    if row is None:
        raise NotFound(f"no repository {repository_id}")
    return row


def _completed(store: ProductStore, repository_id: int) -> Repository:
    """Return the repository row if it has stored results, else raise NotReady."""
    row = _repository(store, repository_id)
    if row.last_completed_at is None:
        raise NotReady(f"repository {repository_id} has no completed analysis")
    return row


def _point(commit: Commit, agg: AggregateMetric) -> dict[str, object]:
    """Return one commit's chart point."""
    return {"sequence": commit.sequence, "sha": commit.sha, "message": commit.message,
            "author": commit.author_name, "timestamp": commit.authored_at,
            "file_count": agg.file_count, "loc": agg.total_loc, "sloc": agg.total_sloc,
            "mean_cc": agg.mean_cc, "mean_mi": agg.mean_mi,
            "convention": agg.convention, "refactor": agg.refactor, "warning": agg.warning,
            "error": agg.error, "total_smells": agg.total_smells,
            "smell_density": agg.mean_smell_density, "debt_score": agg.debt_score,
            "debt_delta": agg.debt_delta, "classification": agg.classification,
            "file_errors": commit.file_errors}


def _aggregate(point: dict[str, object]) -> CommitAggregate:
    """Return the scoring view of a chart point, for the summary."""
    return CommitAggregate(
        sequence=int(point["sequence"]), file_count=int(point["file_count"]),
        total_loc=int(point["loc"]), total_sloc=int(point["sloc"]),
        mean_cc=float(point["mean_cc"]), mean_mi=float(point["mean_mi"]),
        convention=int(point["convention"]), refactor=int(point["refactor"]),
        warning=int(point["warning"]), error=int(point["error"]),
        total_smells=int(point["total_smells"]),
        mean_smell_density=float(point["smell_density"]),
        debt_score=float(point["debt_score"]), debt_delta=float(point["debt_delta"]),
        classification=str(point["classification"]))
