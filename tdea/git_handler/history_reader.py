"""HistoryReader: the clone record and the first-parent history of an RG1 repository (FR-49).

clone_metadata() gives the repository record FR-49 asks for: the address cloned, the clone
time in UTC, the HEAD SHA at clone and the default branch. HEAD and the default branch
are read from the clone's origin/HEAD, which no later checkout moves.

first_parent(head, cutoff) follows only first parents from ``head`` (the HEAD at clone,
or a pinned HEAD) to the root and returns the commits in root-to-HEAD order. A commit
is kept when its committer date is at or before the cut-off; later commits are left out
and counted. Each commit's first-parent position counts from the root (root = 0) along
the whole chain, so positions do not depend on the cut-off. Merge commits are kept with
both parents and the merge flag (D-10). Commits are read from the object database;
nothing is checked out and no network is used.

Resuming from a recorded HEAD that a fresh clone lacks (exit 3, "history changed") is
FR-48 run control, added with the run command on Day 6.

FR-50 (Day 3): read_blob() returns a file's exact bytes from the object database, and
file_changes() returns a commit's file-change records against its first parent (the
empty tree for the root commit). Git runs with fixed options: -M50% (D-11), a fixed
rename limit of 1000, the Myers algorithm with git's default indent heuristic, no context
lines, no external diff or textconv, and git's default big-file threshold. It runs in an
environment with every inherited GIT_ variable removed, no system or global
configuration (HOME and XDG_CONFIG_HOME point nowhere, so this also holds before git
2.32) and no system or global attributes. It also runs without an index: git reuses a
checked-out file instead of its blob when the index marks the file clean, and a clone
checked out with CRLF conversion (Git for Windows' default) would then give wrong hunks.
The repository's own .gitattributes still apply, as they do for git diff -M (RE-03's
oracle). diff_settings() reports these settings with the git and GitPython versions for
the run metadata (FR-52).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import git
from git import BadName, GitCommandError
from git.exc import BadObject
from git.objects import Commit
from git.util import hex_to_bin

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.file_changes import FileChange, parse_changes
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit

_ORIGIN_HEAD = "refs/remotes/origin/HEAD"
_ORIGIN_PREFIX = "refs/remotes/origin/"

RENAME_THRESHOLD = 50  # percent; Git's default (D-11)
RENAME_LIMIT = 1000
DIFF_ALGORITHM = "myers"
BIG_FILE_THRESHOLD = "512m"  # Git's default
_DIFF_OPTIONS = (
    "-r", "--no-commit-id", f"-M{RENAME_THRESHOLD}%", f"-l{RENAME_LIMIT}",
    f"--diff-algorithm={DIFF_ALGORITHM}", "--indent-heuristic", "--no-ext-diff",
    "--no-textconv", "--no-color", "--full-index",
)
_NO_CONFIG = "tdea-no-global-config"  # never created: git reads it as empty
_NO_INDEX = "tdea-no-index"  # never created: git sees an empty index


@dataclass(frozen=True)
class CloneMetadata:
    """The FR-49 repository record, without the cut-off (a run parameter)."""

    url: str
    cloned_at: datetime
    head_sha: str
    default_branch: str


@dataclass(frozen=True)
class FirstParentHistory:
    """The commits of one first-parent history at or before a cut-off."""

    head: str
    cutoff: datetime
    commits: tuple[ResearchCommit, ...]
    excluded_after_cutoff: int


class HistoryReader:
    """Reads the research history of one cloned repository."""

    def __init__(self, local: LocalRepository) -> None:
        """Read from ``local``, which must already hold a clone."""
        self._local = local

    def clone_metadata(self) -> CloneMetadata:
        """Return the address, clone time, HEAD SHA at clone and default branch."""
        repo = self._local.repo
        if self._local.source is None or self._local.cloned_at is None:
            raise RuntimeError("clone() must succeed before clone_metadata()")
        try:
            branch_ref = repo.git.symbolic_ref(_ORIGIN_HEAD).strip()
            head_sha = repo.git.rev_parse(f"{_ORIGIN_HEAD}^{{commit}}").strip()
        except GitCommandError as exc:
            raise _history_error("The clone does not record its default branch.") from exc
        return CloneMetadata(
            url=self._local.source,
            cloned_at=self._local.cloned_at,
            head_sha=head_sha,
            default_branch=branch_ref.removeprefix(_ORIGIN_PREFIX),
        )

    def first_parent(self, head: str, cutoff: datetime) -> FirstParentHistory:
        """Return the first-parent history from root to ``head`` at or before ``cutoff``."""
        cutoff = _require_utc(cutoff, "cut-off")
        repo = self._local.repo
        try:
            head_commit = repo.commit(head)
            chain = repo.git.rev_list("--first-parent", "--reverse", head_commit.hexsha).split()
        except (BadName, ValueError, GitCommandError) as exc:
            raise _history_error(f"{head!r} is not a commit in this clone.") from exc
        commits = []
        excluded = 0
        for position, sha in enumerate(chain):
            commit = repo.commit(sha)
            committed_at = _utc(commit.committed_datetime)
            if committed_at > cutoff:
                excluded += 1
                continue
            commits.append(_research_commit(commit, position, committed_at))
        return FirstParentHistory(head_commit.hexsha, cutoff, tuple(commits), excluded)

    def read_blob(self, sha: str, path: str | None = None) -> bytes:
        """Return a file's exact bytes from the object database; nothing is checked out.

        With ``path`` (POSIX, relative to the repository root), ``sha`` names a commit
        and the file is read at that commit. Without it, ``sha`` is a full blob SHA, as
        a FileChange side records it.
        """
        repo = self._local.repo
        try:
            if path is None:
                binsha = hex_to_bin(sha)
                if repo.odb.info(binsha).type != b"blob":
                    raise ValueError("not a blob")
                return repo.odb.stream(binsha).read()
            item = repo.commit(sha).tree / path
            if item.type != "blob":
                raise ValueError("not a file")
            return item.data_stream.read()
        except (BadName, BadObject, KeyError, ValueError, TypeError) as exc:
            where = sha if path is None else f"{path} at {sha}"
            raise TdeaError(
                ErrorCode.HISTORY_FAILED,
                "File version unreadable",
                f"{where!r} is not a file in this clone.",
                "Check the commit SHA and the path.",
            ) from exc

    def file_changes(self, commit: ResearchCommit | str) -> tuple[FileChange, ...]:
        """Return the commit's file-change records against its first parent (FR-50).

        A merge commit is diffed against its first parent only; the root commit against
        the empty tree.
        """
        sha = commit.sha if isinstance(commit, ResearchCommit) else commit
        try:
            resolved = self._local.repo.commit(sha)
            trees = ([resolved.parents[0].hexsha, resolved.hexsha] if resolved.parents
                     else ["--root", resolved.hexsha])  # reading parents loads the commit
        except (BadName, ValueError) as exc:
            raise _history_error(f"{sha!r} is not a commit in this clone.") from exc
        try:
            raw = self._diff_tree("--raw", "-z", *trees)
            patch = self._diff_tree("-p", "--unified=0", *trees)
            return parse_changes(raw, patch)
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError) as exc:
            raise _history_error(
                f"git could not diff {resolved.hexsha} against its first parent.") from exc

    def diff_settings(self) -> dict[str, object]:
        """Return the fixed diff options and the git and GitPython versions (FR-50, FR-52)."""
        return {
            "rename_threshold_percent": RENAME_THRESHOLD,
            "rename_limit": RENAME_LIMIT,
            "diff_algorithm": DIFF_ALGORITHM,
            "big_file_threshold": BIG_FILE_THRESHOLD,
            "git_version": ".".join(str(part) for part in self._local.repo.git.version_info),
            "gitpython_version": git.__version__,
        }

    def _diff_tree(self, *args: str) -> bytes:
        """Run git diff-tree with the fixed options in an environment free of git settings."""
        repo = self._local.repo
        git_dir = Path(repo.git_dir)
        missing = str(git_dir / _NO_CONFIG)
        command = [repo.git.GIT_PYTHON_GIT_EXECUTABLE, "-c", f"core.attributesFile={missing}",
                   "-c", f"core.bigFileThreshold={BIG_FILE_THRESHOLD}", "diff-tree",
                   *_DIFF_OPTIONS, *args]
        return subprocess.run(command, cwd=repo.working_dir, env=_isolated_env(git_dir),
                              capture_output=True, check=True).stdout


def _isolated_env(git_dir: Path) -> dict[str, str]:
    """Return the caller's environment without GIT_ variables, git settings or an index."""
    missing = str(git_dir / _NO_CONFIG)
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith("GIT_")}
    environment.update({
        "GIT_DIR": str(git_dir),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": missing,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_INDEX_FILE": str(git_dir / _NO_INDEX),
        "HOME": missing,
        "XDG_CONFIG_HOME": missing,
        "LC_ALL": "C",
        "LANGUAGE": "C",
    })
    return environment


def _research_commit(commit: Commit, position: int, committed_at: datetime) -> ResearchCommit:
    """Build the ResearchCommit for one GitPython commit."""
    message = commit.message
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    return ResearchCommit(
        sha=commit.hexsha,
        position=position,
        parents=tuple(parent.hexsha for parent in commit.parents),
        authored_at=_utc(commit.authored_datetime),
        committed_at=committed_at,
        raw=RawCommitStrings(
            author_name=commit.author.name or "",
            author_email=commit.author.email or "",
            committer_name=commit.committer.name or "",
            committer_email=commit.committer.email or "",
            message=message,
        ),
    )


def _utc(moment: datetime) -> datetime:
    """Return a timezone-aware git time in UTC."""
    return moment.astimezone(timezone.utc)


def _require_utc(moment: datetime, name: str) -> datetime:
    """Return ``moment`` in UTC; refuse a naive datetime."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"the {name} must be a timezone-aware datetime")
    return _utc(moment)


def _history_error(cause: str) -> TdeaError:
    """Return the structured error for a history that cannot be read."""
    return TdeaError(
        ErrorCode.HISTORY_FAILED,
        "Commit history unreadable",
        cause,
        "Check the pinned HEAD, or clone the repository again.",
    )
