"""LintCache (NFR-25): keys, hits, misses and exact round-trips in lintcache.db.

The key is blob SHA, file name and profile hash (NFR-25, Appendix C). These tests use
constructed lint results and need no Pylint, so they run in every CI cell; the hit-equals-
fresh-lint check with real Pylint runs is tests/research/test_lint_cache_profile.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from tdea.aggregator.lint_cache import LintCache
from tdea.analyzer.research.profile import (FAILED, OK, SYNTAX_ERROR, LintCachePort,
                                            LintMessage, LintResult)

BLOB = "1" * 40
PROFILE = "p" * 64
RESULT = LintResult(OK, None, (
    LintMessage("unused-import", "W0611", "warning", "Unused import os", 3, ""),
    LintMessage("f-string-without-interpolation", "W1309", "warning", "Using an f-string",
                11, "Cart.total"),
    LintMessage("unused-import", "W0611", "warning", "Unused import sys", 1, ""),
))


@pytest.fixture(name="cache")
def _cache(tmp_path: Path) -> LintCache:
    """Return a lint cache in a fresh lintcache.db."""
    with LintCache(tmp_path / "lintcache.db") as cache:
        yield cache


def test_miss_then_hit_returns_exactly_what_was_stored(cache: LintCache) -> None:
    """A miss returns None; after put, the same key returns an equal result, in order."""
    assert cache.get(BLOB, "a.py", PROFILE) is None
    cache.put(BLOB, "a.py", PROFILE, RESULT)
    assert cache.get(BLOB, "a.py", PROFILE) == RESULT
    assert [m.line for m in cache.get(BLOB, "a.py", PROFILE).messages] == [3, 11, 1]


@pytest.mark.parametrize("blob, name, profile", [
    ("2" * 40, "a.py", PROFILE), (BLOB, "b.py", PROFILE), (BLOB, "a.py", "q" * 64)])
def test_any_other_blob_name_or_profile_is_a_miss(
    cache: LintCache, blob: str, name: str, profile: str
) -> None:
    """A different blob, file name or profile hash is a different key."""
    cache.put(BLOB, "a.py", PROFILE, RESULT)
    assert cache.get(blob, name, profile) is None


def test_failed_and_empty_results_round_trip(cache: LintCache) -> None:
    """A failed file's status and reason, and a clean file's empty result, are kept."""
    failed = LintResult(FAILED, SYNTAX_ERROR, ())
    clean = LintResult(OK, None, ())
    cache.put(BLOB, "broken.py", PROFILE, failed)
    cache.put(BLOB, "clean.py", PROFILE, clean)
    assert cache.get(BLOB, "broken.py", PROFILE) == failed
    assert cache.get(BLOB, "clean.py", PROFILE) == clean


@pytest.mark.parametrize("result", [
    LintResult(OK, SYNTAX_ERROR, ()), LintResult(FAILED, None, ()),
    LintResult(FAILED, "flaky", ()), LintResult("skipped", None, ())])
def test_status_and_reason_must_agree(cache: LintCache, result: LintResult) -> None:
    """ok has no reason; failed has one of FR-51 (e)'s four; a refused value is raised."""
    with pytest.raises(IntegrityError):
        cache.put(BLOB, "a.py", PROFILE, result)
    assert cache.get(BLOB, "a.py", PROFILE) is None


def test_an_entry_is_never_replaced(cache: LintCache) -> None:
    """A second put under the same key keeps the first entry."""
    cache.put(BLOB, "a.py", PROFILE, RESULT)
    cache.put(BLOB, "a.py", PROFILE, LintResult(OK, None, ()))
    assert cache.get(BLOB, "a.py", PROFILE) == RESULT


def test_entries_survive_reopening(tmp_path: Path) -> None:
    """lintcache.db is shared across runs: a new LintCache sees earlier entries."""
    with LintCache(tmp_path / "lintcache.db") as cache:
        cache.put(BLOB, "a.py", PROFILE, RESULT)
    with LintCache(tmp_path / "lintcache.db") as cache:
        assert cache.get(BLOB, "a.py", PROFILE) == RESULT


def test_lint_cache_implements_the_analyzer_port(cache: LintCache) -> None:
    """LintCache offers the LintCachePort methods the analyzer calls."""
    port: LintCachePort = cache
    assert callable(port.get) and callable(port.put)
    