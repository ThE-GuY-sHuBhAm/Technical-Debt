"""RE-06 (FR-52), schema part: separate stores, the 21 entities and their constraints.

The parts of RE-06 that need the run command (steps 1, 3 and 4: run, read the run
metadata, re-run with W = 9) arrive on Day 6. Here: research tables exist only in
research.db and lint-cache tables only in lintcache.db; neither holds a product table
(tdea.db's four entities, FR-19) and neither creates tdea.db; the schema is deterministic,
transactional and enforces its keys; every FR-52 metadata field has a column. These tests
need no Pylint, so they run in every CI cell.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from tdea.aggregator import research_models as rm
from tdea.aggregator.lint_cache import LINT_CACHE_TABLES, LintCache
from tdea.aggregator.research_store import ResearchStore

# Freeze section 7 / SRS Appendix C, typed out here: 21 entities in research.db.
FROZEN_RESEARCH = {"run", "repository", "unit", "unit_summary", "commit", "file_change",
                   "debt_item", "disappearance", "item_mode", "base_rate", "covariate",
                   "bundle", "variant", "task", "declared_opportunity", "output", "execution",
                   "test_result", "detection", "symbol_count", "type_count"}
FROZEN_LINT_CACHE = {"lint_cache", "lint_message"}
PRODUCT_TABLES = {"repositories", "commits", "file_metrics", "aggregate_metrics"}  # FR-19
FR52_FIELDS = {  # FR-52 run metadata field -> where it is stored
    "identifier": ("run", "run_id"), "kind": ("run", "kind"),
    "TDEA version": ("run", "tdea_version"), "interpreter version": ("run", "python_version"),
    "Pylint version": ("run", "pylint_version"), "profile hash": ("run", "profile_hash"),
    "profile": ("run", "profile"), "canonical run file": ("run", "canonical_run_file"),
    "windows (start)": ("unit", "window_start"), "windows (end)": ("unit", "window_end"),
    "W": ("run", "w_months"), "tau": ("run", "tau"), "cut-off": ("run", "cutoff"),
    "clone times": ("repository", "clone_time"), "HEAD SHAs": ("repository", "head_sha"),
    "smell mapping": ("bundle", "smell_mapping"), "seeds": ("run", "seeds"),
    "key fingerprint": ("run", "key_fingerprint"),
    "rename threshold": ("run", "rename_threshold"),
    "astroid version": ("run", "astroid_version"), "radon version": ("run", "radon_version"),
    "git version": ("run", "git_version"), "GitPython version": ("run", "gitpython_version"),
    "rule-list versions": ("run", "rule_list_versions"),
    "PREPARE_MODULE_VERSION": ("run", "prepare_module_version"),
    "start time": ("run", "started_at"), "end time": ("run", "ended_at"),
    "status": ("run", "status"),
    "per-file time limit (FR-51: recorded)": ("run", "time_limit_seconds"),
}
KEY = "a" * 64


def tables(path: Path) -> set[str]:
    """Return the table names in a SQLite file, read with sqlite3 directly."""
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}


def schema(path: Path) -> list[str]:
    """Return the CREATE statements of a SQLite file, in name order."""
    with sqlite3.connect(path) as connection:
        return [row[0] for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]


def a_run(run_id: str = "0123456789ab") -> rm.Run:
    """Return a minimal RG1 run row (test data)."""
    return rm.Run(run_id=run_id, kind="rg1", status="running", tdea_version="0.1.0",
                  python_version="3.11.9", pylint_version="4.1.1", astroid_version="4.3.3",
                  radon_version="6.0.1", profile_hash="p" * 64, profile="{}",
                  canonical_run_file="{}")


def a_repository(run_id: str = "0123456789ab") -> rm.Repository:
    """Return a minimal repository row (test data)."""
    return rm.Repository(run_id=run_id, url="https://github.com/example/project",
                         default_branch="main", clone_time="2026-10-12T00:00:00+00:00",
                         head_sha="b" * 40, cutoff="2026-10-12T00:00:00+00:00")


def a_commit(repository_id: int, **keys: str | None) -> rm.Commit:
    """Return a commit row with the given identity keys (test data)."""
    return rm.Commit(repository_id=repository_id, sha="c" * 40, first_parent_position=0,
                     parent_shas="[]", is_merge=False, authored_at="2026-01-01T00:00:00+00:00",
                     committed_at="2026-01-01T00:00:00+00:00", message="Initial", **keys)


def test_each_file_holds_only_its_own_tables(tmp_path: Path) -> None:
    """research.db has the 21 research tables, lintcache.db the 2; no product table; no tdea.db."""
    ResearchStore(tmp_path / "research.db").close()
    LintCache(tmp_path / "lintcache.db").close()
    assert tables(tmp_path / "research.db") == FROZEN_RESEARCH
    assert tables(tmp_path / "lintcache.db") == FROZEN_LINT_CACHE
    assert set(rm.RESEARCH_TABLES) == FROZEN_RESEARCH and len(rm.RESEARCH_TABLES) == 21
    assert set(LINT_CACHE_TABLES) == FROZEN_LINT_CACHE
    assert not (FROZEN_RESEARCH | FROZEN_LINT_CACHE) & PRODUCT_TABLES
    assert sorted(path.name for path in tmp_path.iterdir()) == ["lintcache.db", "research.db"]


@pytest.mark.parametrize("existing", sorted(FROZEN_LINT_CACHE | PRODUCT_TABLES))
def test_mixing_files_is_refused(tmp_path: Path, existing: str) -> None:
    """A research store never opens a file that holds lint-cache or product tables."""
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as connection:
        connection.execute(f"CREATE TABLE {existing} (id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="not a research store"):
        ResearchStore(path)
    assert tables(path) == {existing}


def test_lint_cache_never_opens_research_db(tmp_path: Path) -> None:
    """The lint cache refuses research.db, so the two files are never mixed."""
    ResearchStore(tmp_path / "research.db").close()
    with pytest.raises(ValueError, match="not a lint cache"):
        LintCache(tmp_path / "research.db")


def test_schema_is_deterministic_and_reopening_keeps_data(tmp_path: Path) -> None:
    """Two fresh stores have the same schema; reopening keeps rows and changes nothing."""
    for name in ("one.db", "two.db"):
        ResearchStore(tmp_path / name).close()
    assert schema(tmp_path / "one.db") == schema(tmp_path / "two.db")
    with ResearchStore(tmp_path / "one.db") as store, store.transaction() as session:
        session.add(a_run())
    with ResearchStore(tmp_path / "one.db") as store, store.reader() as session:
        assert session.get(rm.Run, "0123456789ab").kind == "rg1"
    assert schema(tmp_path / "one.db") == schema(tmp_path / "two.db")


def test_transaction_is_all_or_nothing(tmp_path: Path) -> None:
    """An error inside a transaction leaves none of its rows behind."""
    with ResearchStore(tmp_path / "research.db") as store:
        with pytest.raises(RuntimeError):
            with store.transaction() as session:
                session.add(a_run())
                session.flush()
                session.add(a_repository())
                session.flush()
                raise RuntimeError("unit failed")
        with store.reader() as session:
            assert session.get(rm.Run, "0123456789ab") is None


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """A commit of a repository that does not exist is refused."""
    with ResearchStore(tmp_path / "research.db") as store:
        with pytest.raises(IntegrityError):
            with store.transaction() as session:
                session.add(a_commit(repository_id=999))


@pytest.mark.parametrize("column", ["author_name_key", "author_email_key",
                                    "committer_name_key", "committer_email_key"])
@pytest.mark.parametrize("value", ["asha.rao@example.invalid", "Asha Rao", "A" * 64,
                                   "a" * 63, "a" * 64 + "\x00Asha Rao <asha.rao@example.invalid>",
                                   ("a" * 64).encode("ascii")])
def test_identity_columns_accept_keys_only(tmp_path: Path, column: str,
                                           value: str | bytes) -> None:
    """A raw name or e-mail, or anything but 64 lowercase hex text, is refused (NFR-27).

    A key followed by a NUL and a raw identity, and 64 hex bytes stored as a BLOB, are
    refused too: SQLite's length() and GLOB alone would stop at the NUL.
    """
    with ResearchStore(tmp_path / "research.db") as store:
        with store.transaction() as session:
            session.add(a_run())
            session.flush()
            session.add(a_repository())
        with pytest.raises(IntegrityError):
            with store.transaction() as session:
                session.add(a_commit(1, **{column: value}))
        with store.transaction() as session:
            session.add(a_commit(1, **{column: KEY}))


def test_trace_status_may_be_unclassified_but_never_human(tmp_path: Path) -> None:
    """NULL (not classified, Task 6 decision 4) and the three statuses only; never human."""
    with ResearchStore(tmp_path / "research.db") as store:
        with store.transaction() as session:
            session.add(a_run())
            session.flush()
            session.add(a_repository())
            session.flush()
            session.add(a_commit(1))
        with store.reader() as session:
            assert session.get(rm.Commit, 1).trace_status is None
        with pytest.raises(IntegrityError):
            with store.transaction() as session:
                session.get(rm.Commit, 1).trace_status = "human"


def test_rg10_keys_are_scoped_by_bundle(tmp_path: Path) -> None:
    """Two bundles may both have task-1; one bundle may not have it twice."""
    with ResearchStore(tmp_path / "research.db") as store:
        with store.transaction() as session:
            for number in (1, 2):
                run_id = f"rg10-run-{number}"
                session.add(a_run(run_id))
                session.flush()
                session.add(rm.Bundle(run_id=run_id, path="b", schema_version="1",
                                      harness_version="1", prepare_module_version="1",
                                      env_fingerprint="{}", smell_mapping="{}",
                                      content_hash="h"))
                session.flush()
                session.add(rm.Task(bundle_id=number, task_id="task-1", project="p",
                                    target_module="m.py", marker="# M", function_names="[]",
                                    hidden_test_ids="[]", project_test_ids="[]"))
        with pytest.raises(IntegrityError):
            with store.transaction() as session:
                session.add(rm.Task(bundle_id=1, task_id="task-1", project="p",
                                    target_module="m.py", marker="# M", function_names="[]",
                                    hidden_test_ids="[]", project_test_ids="[]"))


def test_every_fr52_metadata_field_has_a_column(tmp_path: Path) -> None:
    """Each field FR-52 lists for a run is stored in the run or its parent chain."""
    with ResearchStore(tmp_path / "research.db") as store:
        inspector = inspect(store._engine)  # pylint: disable=protected-access
        columns = {table: {column["name"] for column in inspector.get_columns(table)}
                   for table in FROZEN_RESEARCH}
    missing = [field for field, (table, column) in FR52_FIELDS.items()
               if column not in columns[table]]
    assert not missing


def test_no_column_can_hold_a_raw_identity(tmp_path: Path) -> None:
    """Name- and e-mail-like columns are identity keys or flags, never raw values."""
    allowed = {"author_name_key", "author_email_key", "committer_name_key",
               "committer_email_key", "function_names", "same_name", "same_email"}
    with ResearchStore(tmp_path / "research.db") as store:
        inspector = inspect(store._engine)  # pylint: disable=protected-access
        named = {column["name"] for table in FROZEN_RESEARCH
                 for column in inspector.get_columns(table)
                 if set(re.split(r"_", column["name"])) & {"name", "names", "email", "mail",
                                                           "author", "committer"}}
    assert named <= allowed | {"author_id"}


def test_helpers_write_canonical_json_and_utc_text() -> None:
    """List columns use canonical JSON; timestamps must be UTC."""
    assert rm.canonical_json({"b": [2, 1], "a": "é"}) == '{"a":"é","b":[2,1]}'
    assert rm.iso_utc(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "2026-01-01T00:00:00+00:00"
    with pytest.raises(ValueError):
        rm.iso_utc(datetime(2026, 1, 1))
        