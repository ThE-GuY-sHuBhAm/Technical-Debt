"""Deterministic git for the fixture builders (FX-RG1, FX-PAIRS, FX-S, FX-ERR).

Every git call runs with no system configuration, a global configuration file that does
not exist and no system or global attributes file, so nothing on the machine can change
the result. Each commit gets its identities and dates (with explicit UTC offsets) on the
command line, files are written as exact bytes, and line-ending conversion and signing
are off. The same builder call therefore gives the same commit SHAs on any machine, in
any time zone, at any time.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_FIXED_OPTIONS = [
    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
    "-c", "core.autocrlf=false", "-c", "core.fileMode=false",
    "-c", "init.defaultBranch=main", "-c", "merge.ff=false",
    "-c", "log.showSignature=false",
]


@dataclass(frozen=True)
class Identity:
    """A fixed name and e-mail for an author or committer."""

    name: str
    email: str


def isolated_env(repo: Path) -> dict[str, str]:
    """Return an environment in which git reads no system or global configuration.

    Global attributes are read from $XDG_CONFIG_HOME/git/attributes even without a global
    configuration file, so XDG_CONFIG_HOME points to a folder that does not exist (Task 5).
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(repo / ".git" / "tdea-fixture-no-global-config"),
        "GIT_ATTR_NOSYSTEM": "1",
        "XDG_CONFIG_HOME": str(repo / ".git" / "tdea-fixture-no-xdg-config"),
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def git_output(repo: Path, *args: str) -> str:
    """Run a read-only git command in ``repo`` with isolated configuration."""
    completed = subprocess.run(
        ["git", *_FIXED_OPTIONS, *args], cwd=repo, env=isolated_env(repo),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return completed.stdout


class FixtureRepo:
    """A git repository built step by step from fixed content, identities and dates."""

    def __init__(self, path: Path) -> None:
        """Create an empty repository at ``path`` on branch main."""
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=False)
        self.git("init", "-q")

    @classmethod
    def existing(cls, path: Path) -> FixtureRepo:
        """Return a FixtureRepo for a repository a builder already made at ``path``."""
        repo = cls.__new__(cls)
        repo.path = Path(path)
        if not (repo.path / ".git").is_dir():
            raise ValueError(f"{path} is not a fixture repository")
        return repo

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        """Run git in the repository with isolated configuration and return its output."""
        completed = subprocess.run(
            ["git", *_FIXED_OPTIONS, *args], cwd=self.path,
            env={**isolated_env(self.path), **(env or {})},
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        return completed.stdout

    def write(self, files: dict[str, str | bytes | None]) -> None:
        """Write each file as exact bytes (text as UTF-8 with LF); None deletes the file."""
        for name, content in files.items():
            target = self.path / name
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode())

    def commit(
        self,
        message: str,
        files: dict[str, str | bytes | None],
        *,
        author: Identity,
        date: str,
        committer: Identity | None = None,
        committer_date: str | None = None,
    ) -> str:
        """Write ``files``, stage everything, commit, and return the new commit's SHA."""
        self.write(files)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "--no-verify", "-m", message,
                 env=_commit_env(author, date, committer, committer_date))
        return self.head()

    def merge(self, branch: str, message: str, *, author: Identity, date: str) -> str:
        """Merge ``branch`` into the current branch with a merge commit; return its SHA."""
        self.git("merge", "-q", "--no-ff", "--no-verify", "-m", message, branch,
                 env=_commit_env(author, date, None, None))
        return self.head()

    def head(self) -> str:
        """Return the SHA of HEAD."""
        return self.git("rev-parse", "HEAD").strip()


def _commit_env(
    author: Identity, date: str, committer: Identity | None, committer_date: str | None
) -> dict[str, str]:
    """Return the variables that fix a commit's identities and dates."""
    committer = committer or author
    return {
        "GIT_AUTHOR_NAME": author.name, "GIT_AUTHOR_EMAIL": author.email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_NAME": committer.name, "GIT_COMMITTER_EMAIL": committer.email,
        "GIT_COMMITTER_DATE": committer_date or date,
    }
