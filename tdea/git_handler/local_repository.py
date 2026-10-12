"""FR-06 cloning, FR-07 product history and FR-10 clean-up of one repository.

A LocalRepository owns one clone in a fresh system-managed temporary directory. It is
used as a context manager, so the clone is deleted when the work finishes and also when
any stage fails (FR-10). A failed clone deletes its partial files before the error is
raised.

GitHub repositories are cloned from their HTTPS address, built from the parsed owner and
repository (execution plan, Task 3 decision 5), with no token (FR-04 uses the token for
API requests only) and with git's credential prompts off, so a failure cannot hang. A
local path can be cloned too; the tests use this.

The address cloned from and the clone time (UTC, taken when the clone completes) are
kept for the research history record (FR-49); the clock is replaceable for tests.

A process that is killed cannot delete its own clone. remove_stale_clones() deletes such
leftovers from a clone folder that only one analysis at a time uses; research runs call
it on their own folder while they hold the single-analysis lock (Day 6). The product's
clones in the system temporary directory are not touched.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any

from git import GitCommandError, Repo

from tdea.git_handler.commit_info import CommitInfo
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.url_validator import RepositoryRef

CLONE_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
TEMP_PREFIX = "tdea-clone-"


class LocalRepository:
    """One full clone (all history, all branch references) in a temporary directory."""

    def __init__(
        self,
        temp_root: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Clone under ``temp_root``, or under the system temporary directory if None."""
        self._temp_root = temp_root
        self._clock = clock or _utc_now
        self._path: Path | None = None
        self._repo: Repo | None = None
        self._source: str | None = None
        self._cloned_at: datetime | None = None

    @property
    def path(self) -> Path | None:
        """Return the clone directory, or None before clone() and after cleanup()."""
        return self._path

    @property
    def source(self) -> str | None:
        """Return the address the clone was made from, or None without a clone."""
        return self._source

    @property
    def cloned_at(self) -> datetime | None:
        """Return when the clone completed (UTC), or None without a clone."""
        return self._cloned_at

    @property
    def repo(self) -> Repo:
        """Return the GitPython repository of the clone."""
        if self._repo is None:
            raise RuntimeError("clone() must succeed before the repository is used")
        return self._repo

    def clone(self, source: RepositoryRef | Path) -> Path:
        """Clone ``source`` into a new temporary directory and return that directory."""
        if self._path is not None:
            raise RuntimeError("this LocalRepository already holds a clone")
        address = source.clone_url if isinstance(source, RepositoryRef) else str(source)
        self._path = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=self._temp_root))
        try:
            self._repo = Repo.clone_from(address, self._path, env=CLONE_ENV)
            self._source = address
            self._cloned_at = _as_utc(self._clock())
        except GitCommandError as exc:
            self.cleanup()
            raise TdeaError(
                ErrorCode.CLONE_FAILED,
                "Clone failed",
                "Git could not copy the repository; the connection may have dropped.",
                "Check the internet connection and try again.",
            ) from exc
        except BaseException:
            self.cleanup()
            raise
        return self._path

    def history(self) -> list[CommitInfo]:
        """Return every commit reachable from HEAD, in git log order (FR-07)."""
        if self._repo is None:
            raise RuntimeError("clone() must succeed before history()")
        try:
            return [CommitInfo.from_commit(commit) for commit in self._repo.iter_commits("HEAD")]
        except (GitCommandError, ValueError) as exc:
            raise TdeaError(
                ErrorCode.HISTORY_FAILED,
                "Commit history unreadable",
                "Git could not read the cloned repository's history.",
                "Try the analysis again; if it fails again, the repository may be damaged.",
            ) from exc

    def cleanup(self) -> None:
        """Delete the clone and everything in it; safe to call more than once (FR-10)."""
        if self._repo is not None:
            self._repo.close()
            self._repo = None
        if self._path is not None:
            remove_tree(self._path)
            self._path = None
        self._source = None
        self._cloned_at = None

    def __enter__(self) -> LocalRepository:
        """Return this repository; the clone is deleted when the block ends."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Delete the clone whether the block succeeded or raised."""
        self.cleanup()


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    """Return a timezone-aware time converted to UTC; refuse a naive one."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("the clock must return a timezone-aware time")
    return moment.astimezone(timezone.utc)


def remove_stale_clones(folder: Path) -> int:
    """Delete the clones a killed process left in ``folder``; return how many (FR-10).

    Only entries named like a LocalRepository clone (``tdea-clone-*``) are removed; anything
    else in the folder is left alone. A link is unlinked, never followed, and a folder that
    is itself a link is refused (NotADirectoryError), so the clean-up cannot be redirected.
    The caller must hold the single-analysis lock, so no clone in the folder can still be
    in use. The folder is created when missing.
    """
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(folder)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f"{folder} is not a plain folder")
    removed = 0
    for entry in sorted(folder.glob(f"{TEMP_PREFIX}*")):
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink()
        else:
            remove_tree(entry)
        removed += 1
    return removed


def remove_tree(path: Path) -> None:
    """Delete a directory tree, including read-only files (git marks objects read-only)."""
    if not path.exists():
        return
    handler_kind = "onexc" if sys.version_info >= (3, 12) else "onerror"
    options: dict[str, Callable[..., Any]] = {handler_kind: _clear_read_only}
    shutil.rmtree(path, **options)


def _clear_read_only(function: Callable[[str], Any], path: str, _error: Any) -> None:
    """Make ``path`` writable and retry the failed removal (needed on Windows)."""
    os.chmod(path, stat.S_IWRITE)
    function(path)
