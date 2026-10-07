"""FR-02: validate a GitHub repository URL before any other processing.

The pattern is used exactly as SRS v2.3 states it and must match the whole input. It
accepts http:// and https:// URLs; once the owner and repository are parsed, every
request TDEA makes is built from them over HTTPS (SRS 4.4), so no unencrypted request
is ever sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tdea.git_handler.errors import ErrorCode, TdeaError

PATTERN = r"^https?://github\.com/[\w.-]+/[\w.-]+/?$"
_COMPILED = re.compile(PATTERN)


@dataclass(frozen=True)
class RepositoryRef:
    """A GitHub repository named by a URL that passed FR-02."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Return owner/name."""
        return f"{self.owner}/{self.name}"

    @property
    def api_url(self) -> str:
        """Return the FR-03 endpoint for this repository, always over HTTPS."""
        return f"https://api.github.com/repos/{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        """Return the address FR-06 clones from, always over HTTPS."""
        return f"https://github.com/{self.owner}/{self.name}"


class UrlValidator:
    """Checks repository URLs against the FR-02 pattern."""

    PATTERN = PATTERN

    @staticmethod
    def is_valid(url: object) -> bool:
        """Return True if ``url`` is a string the FR-02 pattern matches in full."""
        return isinstance(url, str) and _COMPILED.fullmatch(url) is not None

    @classmethod
    def parse(cls, url: object) -> RepositoryRef:
        """Return the repository a valid URL names; raise TdeaError for any other input."""
        if not isinstance(url, str) or not cls.is_valid(url):
            raise _invalid_url(url)
        owner, name = url.split("://", 1)[1].split("/", 1)[1].rstrip("/").split("/")
        return RepositoryRef(owner, name)


def _invalid_url(url: object) -> TdeaError:
    """Build the FR-05 error for a rejected URL, naming the most likely problem."""
    if not isinstance(url, str) or not url.strip():
        cause = "No repository URL was given."
    elif not url.startswith(("http://", "https://")):
        cause = "The URL does not start with https://github.com/."
    elif url.split("://", 1)[1].split("/", 1)[0] != "github.com":
        cause = "The URL is not a github.com address."
    else:
        cause = ("The URL must be in the form https://github.com/<owner>/<repo>, "
                 "with nothing after the repository name.")
    return TdeaError(
        ErrorCode.INVALID_URL,
        "Invalid repository URL",
        cause,
        "Enter the repository address in the form https://github.com/<owner>/<repo>.",
    )