"""FR-06 cloning, FR-07 history and FR-10 clean-up, on test-built local repositories.

Clean-up is checked after success (TC-032), after a forced mid-clone failure (the Task 3
exit condition) and after an exception inside the context manager (decision 6). Every
clone goes under a test-owned ``clones`` directory, so "no temp directory left" means
that directory is empty.
"""

from __future__ import annotations

import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import git
import pytest
from repo_builder import RepoBuilder

from tdea.git_handler import local_repository
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.url_validator import UrlValidator


@pytest.fixture(name="source")
def _source(tmp_path: Path) -> RepoBuilder:
    """Return a source repository with 5 commits on main and 3 branches in all."""
    builder = RepoBuilder(tmp_path / "source")
    builder.commit("One", {"a.py": "A = 1\n"}, date="2026-09-01T09:00:00+05:30")
    builder.commit("Two", {"b.py": "B = 1\n"}, date="2026-09-02T09:00:00+05:30")
    builder.git("branch", "feature-x")
    builder.git("branch", "feature-y")
    builder.commit("Three", {"a.py": "A = 2\n"}, date="2026-09-03T09:00:00+05:30")
    builder.commit("Four", {"c.py": "C = 1\n"}, date="2026-09-04T09:00:00+05:30")
    builder.commit("Five", {"b.py": None}, date="2026-09-05T09:00:00+05:30")
    return builder


@pytest.fixture(name="clones")
def _clones(tmp_path: Path) -> Path:
    """Return the directory every test clone is created in."""
    path = tmp_path / "clones"
    path.mkdir()
    return path


def leftovers(clones: Path) -> list[Path]:
    """Return whatever is left in the clones directory."""
    return sorted(clones.iterdir())


def test_clone_is_complete_with_all_branches(source: RepoBuilder, clones: Path) -> None:
    """All commits and all branch references arrive (TC-017, TC-018 shape)."""
    with LocalRepository(clones) as local:
        path = local.clone(source.path)
        assert path.parent == clones
        with git.Repo(path) as clone:
            remote_heads = sorted(ref.remote_head for ref in clone.remotes.origin.refs
                                  if ref.remote_head != "HEAD")
            assert remote_heads == ["feature-x", "feature-y", "main"]
            assert len(list(clone.iter_commits("HEAD"))) == 5


def test_history_matches_git_log(source: RepoBuilder, clones: Path) -> None:
    """Hashes and authors equal git log on the source, in its order (TC-020, TC-022 shape)."""
    with LocalRepository(clones) as local:
        local.clone(source.path)
        history = local.history()
    assert [record.sha for record in history] == source.log("%H")
    assert [f"{r.author_name}|{r.author_email}" for r in history] == source.log("%an|%ae")
    assert history[0].authored_at == "2026-09-05T03:30:00Z"


def test_cleanup_after_success(source: RepoBuilder, clones: Path) -> None:
    """The clone is gone once the work completes (TC-032, FR-10)."""
    with LocalRepository(clones) as local:
        path = local.clone(source.path)
        local.history()
        assert path.is_dir()
    assert not path.exists()
    assert local.path is None
    assert not leftovers(clones)


def test_cleanup_after_forced_mid_clone_failure(
    source: RepoBuilder, clones: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clone that fails part-way leaves no temporary directory (Task 3 exit condition)."""

    def fail_part_way(_url: str, to_path: Path, **_kwargs: Any) -> None:
        objects = Path(to_path) / ".git" / "objects" / "pack"
        objects.mkdir(parents=True)
        (objects / "partial.pack").write_bytes(b"PACK" + b"\0" * 64)
        (objects / "partial.pack").chmod(stat.S_IREAD)
        raise git.GitCommandError(["git", "clone"], 128, b"", b"early EOF")

    monkeypatch.setattr(local_repository.Repo, "clone_from", fail_part_way)
    local = LocalRepository(clones)
    with pytest.raises(TdeaError) as caught:
        local.clone(source.path)
    assert caught.value.code is ErrorCode.CLONE_FAILED
    assert "early EOF" not in str(caught.value)
    assert local.path is None
    assert not leftovers(clones)


def test_cleanup_after_real_git_failure(tmp_path: Path, clones: Path) -> None:
    """git itself failing (no repository at the source) also leaves nothing behind."""
    not_a_repository = tmp_path / "plain"
    not_a_repository.mkdir()
    with pytest.raises(TdeaError) as caught:
        LocalRepository(clones).clone(not_a_repository)
    assert caught.value.code is ErrorCode.CLONE_FAILED
    assert not leftovers(clones)


def test_cleanup_after_exception_inside_the_context(source: RepoBuilder, clones: Path) -> None:
    """An error in a later stage still deletes the clone, and the error propagates."""
    with pytest.raises(KeyError, match="later stage"):
        with LocalRepository(clones) as local:
            path = local.clone(source.path)
            raise KeyError("later stage")
    assert not path.exists()
    assert not leftovers(clones)


def test_cleanup_removes_read_only_files_and_is_repeatable(
    source: RepoBuilder, clones: Path
) -> None:
    """Read-only files (as git writes on Windows) are removed; a second cleanup is a no-op."""
    local = LocalRepository(clones)
    path = local.clone(source.path)
    locked = path / "locked.txt"
    locked.write_text("read only", encoding="utf-8")
    locked.chmod(stat.S_IREAD)
    local.cleanup()
    local.cleanup()
    assert not path.exists()
    assert not leftovers(clones)


def test_github_clone_uses_https_and_no_token(
    clones: Path, monkeypatch: pytest.MonkeyPatch, canary_token: str,
    find_canary: Callable[..., list[Path]],
) -> None:
    """An http:// input is cloned over HTTPS, without a token and without prompts."""
    seen: dict[str, Any] = {}

    def record(url: str, to_path: Path, **kwargs: Any) -> None:
        seen.update(url=url, kwargs=kwargs, to_path=to_path)
        raise git.GitCommandError(["git", "clone"], 128)

    monkeypatch.setattr(local_repository.Repo, "clone_from", record)
    with pytest.raises(TdeaError):
        LocalRepository(clones).clone(UrlValidator.parse("http://github.com/psf/requests"))
    assert seen["url"] == "https://github.com/psf/requests"
    assert seen["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert canary_token not in repr(seen)
    assert not find_canary(clones)


def test_history_and_double_clone_need_the_right_state(
    source: RepoBuilder, clones: Path
) -> None:
    """history() needs a clone, and one LocalRepository holds one clone."""
    with LocalRepository(clones) as local:
        with pytest.raises(RuntimeError):
            local.history()
        local.clone(source.path)
        with pytest.raises(RuntimeError):
            local.clone(source.path)
    assert not leftovers(clones)