"""Small git repositories built at test time for the git_handler tests (Task 3).

The workbook fixture FX-S arrives with tests/fixtures/make_fx_product.py on Day 2; until
then these tests build what they need here. Identity, dates and settings are passed on
the command line, so the machine's git configuration cannot change the result.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT = ["git", "-c", "user.name=TDEA Test", "-c", "user.email=test@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
       "-c", "init.defaultBranch=main"]
DEFAULT_DATE = "2026-09-01T10:00:00+00:00"


class RepoBuilder:
    """A git repository under construction in a test's temporary directory."""

    def __init__(self, path: Path) -> None:
        """Create an empty repository at ``path`` on branch main."""
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        """Run git in the repository and return its output."""
        completed = subprocess.run(
            [*GIT, *args], cwd=self.path, capture_output=True, text=True, check=True,
            env={**os.environ, **(env or {})},
        )
        return completed.stdout

    def commit(
        self,
        message: str,
        changes: dict[str, str | None],
        author: tuple[str, str] = ("Asha Rao", "asha@example.invalid"),
        date: str = DEFAULT_DATE,
    ) -> str:
        """Write (or, for None, delete) each file, commit, and return the new SHA."""
        for name, text in changes.items():
            target = self.path / name
            if text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, env={
            "GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
            "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
        })
        return self.head()

    def head(self) -> str:
        """Return the SHA of HEAD."""
        return self.git("rev-parse", "HEAD").strip()

    def log(self, fmt: str) -> list[str]:
        """Return ``git log --format=fmt`` from HEAD, one line per commit (the TC oracle)."""
        return self.git("log", f"--format={fmt}").splitlines()