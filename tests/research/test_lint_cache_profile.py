"""NFR-25 with real lints: a cache hit equals a fresh lint, and only an exact key hits.

The fresh lints are counted by wrapping the profile's own fresh-lint step, so each test
shows exactly when Pylint ran and when the stored result was returned.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tdea.aggregator.lint_cache import LintCache
from tdea.analyzer.research.profile import FAILED, EnvironmentFacts, ResearchProfile

CONTENT = b'"""Module."""\nimport os\n\n\ndef total(values):\n    """Sum."""\n    return values\n'
BLOB = "a" * 40


@pytest.fixture(name="counted")
def _counted(monkeypatch: pytest.MonkeyPatch) -> tuple[ResearchProfile, list[str]]:
    """Return an accepted profile whose fresh lints are recorded by file name.

    CI's test cells are not virtual environments, so the check is given that one fact.
    """
    profile = ResearchProfile()
    profile.check_environment(replace(EnvironmentFacts.current(), in_virtual_environment=True))
    fresh = profile._lint_fresh  # pylint: disable=protected-access
    calls: list[str] = []

    def counting(content: bytes, file_name: str):
        """Record and run one fresh lint."""
        calls.append(file_name)
        return fresh(content, file_name)

    monkeypatch.setattr(profile, "_lint_fresh", counting)
    return profile, calls


def test_miss_lints_and_stores_then_hit_equals_fresh(
    counted: tuple[ResearchProfile, list[str]], tmp_path: Path
) -> None:
    """The first lint runs Pylint and stores; the second returns the equal stored result."""
    profile, calls = counted
    with LintCache(tmp_path / "lintcache.db") as cache:
        fresh = profile.lint(CONTENT, "mod.py", blob_sha=BLOB, cache=cache)
        hit = profile.lint(CONTENT, "mod.py", blob_sha=BLOB, cache=cache)
    assert calls == ["mod.py"]
    assert hit == fresh == ResearchProfile().lint(CONTENT, "mod.py")
    assert [m.message_id for m in hit.messages] == ["W0611"]


def test_other_blob_name_or_profile_lints_again(
    counted: tuple[ResearchProfile, list[str]], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different blob SHA, file name or profile hash is a miss and lints afresh."""
    profile, calls = counted
    with LintCache(tmp_path / "lintcache.db") as cache:
        profile.lint(CONTENT, "mod.py", blob_sha=BLOB, cache=cache)
        profile.lint(CONTENT, "mod.py", blob_sha="b" * 40, cache=cache)
        profile.lint(CONTENT, "other.py", blob_sha=BLOB, cache=cache)
        monkeypatch.setattr(profile, "profile_hash", lambda: "f" * 64)
        profile.lint(CONTENT, "mod.py", blob_sha=BLOB, cache=cache)
    assert calls == ["mod.py", "mod.py", "other.py", "mod.py"]


def test_failed_lints_are_cached_like_any_result(
    counted: tuple[ResearchProfile, list[str]], tmp_path: Path
) -> None:
    """A file that fails to lint is cached with its reason; the hit equals the fresh result."""
    profile, calls = counted
    with LintCache(tmp_path / "lintcache.db") as cache:
        fresh = profile.lint(b"def broken(:\n", "broken.py", blob_sha=BLOB, cache=cache)
        hit = profile.lint(b"def broken(:\n", "broken.py", blob_sha=BLOB, cache=cache)
    assert calls == ["broken.py"] and hit == fresh and fresh.status == FAILED


def test_no_cache_without_a_blob_sha(
    counted: tuple[ResearchProfile, list[str]], tmp_path: Path
) -> None:
    """Without a blob SHA there is no key, so every lint is fresh and nothing is stored."""
    profile, calls = counted
    with LintCache(tmp_path / "lintcache.db") as cache:
        profile.lint(CONTENT, "mod.py", cache=cache)
        profile.lint(CONTENT, "mod.py", cache=cache)
        assert cache.get(BLOB, "mod.py", profile.profile_hash()) is None
    assert calls == ["mod.py", "mod.py"]
    