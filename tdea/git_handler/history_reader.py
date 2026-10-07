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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from git import BadName, GitCommandError
from git.objects import Commit

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit

_ORIGIN_HEAD = "refs/remotes/origin/HEAD"
_ORIGIN_PREFIX = "refs/remotes/origin/"


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