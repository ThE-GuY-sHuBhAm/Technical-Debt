"""Product-preservation guard: research commits change nothing under dashboard/.

TDEA adds no research screen, so the web interface and its browser cases stay as they
are (execution plan, section 6). This script reads the git history and fails if a
research commit adds, changes, deletes or renames a file under dashboard/.

A commit counts as a research commit when either of these holds:

* its subject starts with ``research:`` or ``research(<scope>):``;
* it touches a research path: a path with a component whose name starts with
  "research" (tdea/aggregator/research_job.py, tests/research/...) or is rg1 or rg10.

The second rule catches research work committed without the prefix; research code that
lives outside such paths is caught only by the prefix. Merge commits are skipped,
because each commit they bring in is checked on its own. Renames are split into a
deletion and an addition, so moving a file out of dashboard/ is a change under it.

The whole history reachable from HEAD is checked by default, so the result does not
depend on how CI was triggered.

Exit status: 0 no research commit changes dashboard/; 1 at least one does; 2 the
history cannot be read (not a git repository, no commits, or a shallow clone).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DASHBOARD = "dashboard"
_RESEARCH_SUBJECT = re.compile(r"^research(\([^)]*\))?!?:", re.IGNORECASE)
_RESEARCH_COMPONENT = re.compile(r"^(research|rg10?(_|$))", re.IGNORECASE)
_RECORD = "\x1e"
_FIELD = "\x1f"

EXIT_PASS = 0
EXIT_VIOLATION = 1
EXIT_CANNOT_READ = 2


class HistoryError(Exception):
    """The git history cannot be read completely."""


@dataclass(frozen=True)
class Commit:
    """One non-merge commit: its hash, subject and the paths it changes."""

    sha: str
    subject: str
    paths: tuple[str, ...]


def is_research_path(path: str) -> bool:
    """Return True if any component of ``path`` names research code or data."""
    return any(
        _RESEARCH_COMPONENT.match(part.split(".", 1)[0])
        for part in PurePosixPath(path).parts
    )


def is_dashboard_path(path: str, dashboard: str = DASHBOARD) -> bool:
    """Return True if ``path`` lies under the top-level dashboard directory."""
    parts = PurePosixPath(path).parts
    return len(parts) > 1 and parts[0] == dashboard


def research_reason(commit: Commit) -> str | None:
    """Return why ``commit`` is a research commit, or None if it is not one."""
    if _RESEARCH_SUBJECT.match(commit.subject):
        return "subject starts with research:"
    for path in commit.paths:
        if is_research_path(path):
            return f"touches research path {path}"
    return None


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its standard output."""
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except OSError as exc:
        raise HistoryError(f"cannot run git: {exc}") from exc
    if completed.returncode != 0:
        raise HistoryError(completed.stderr.strip() or f"git {args[0]} failed")
    return completed.stdout


def read_commits(repo: Path, revisions: Sequence[str] = ("HEAD",)) -> list[Commit]:
    """Return the non-merge commits reachable from ``revisions`` with their changed paths."""
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise HistoryError("the clone is shallow; check out the full history "
                           "(actions/checkout with fetch-depth: 0)")
    output = _git(repo, "log", "--no-merges", "--no-renames", "--name-only",
                  f"--format={_RECORD}%H{_FIELD}%s", *revisions, "--")
    commits = []
    for record in output.split(_RECORD)[1:]:
        header, _, body = record.partition("\n")
        sha, _, subject = header.partition(_FIELD)
        paths = tuple(line for line in body.splitlines() if line)
        commits.append(Commit(sha, subject, paths))
    return commits


def violations(commits: Sequence[Commit], dashboard: str = DASHBOARD) -> list[str]:
    """Return one message per research commit that changes the dashboard directory."""
    found = []
    for commit in commits:
        touched = [path for path in commit.paths if is_dashboard_path(path, dashboard)]
        reason = research_reason(commit) if touched else None
        if reason:
            listed = "\n".join(f"    {path}" for path in touched)
            found.append(f"{commit.sha[:12]} {commit.subject}\n  ({reason}) changes:\n{listed}")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Check the history and return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("revisions", nargs="*", default=["HEAD"],
                        help="revisions or ranges to check (default: all history from HEAD)")
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="repository directory (default: current directory)")
    parser.add_argument("--dashboard", default=DASHBOARD,
                        help="top-level dashboard directory (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        commits = read_commits(args.repo, args.revisions)
    except HistoryError as exc:
        print(f"Cannot read the history: {exc}", file=sys.stderr)
        return EXIT_CANNOT_READ
    found = violations(commits, args.dashboard)
    for message in found:
        print(message)
    print(f"{len(commits)} commits checked; {len(found)} research commits change "
          f"{args.dashboard}/.")
    return EXIT_VIOLATION if found else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
