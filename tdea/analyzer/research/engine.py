"""ResearchEngine: the analyzer's facade for the research engine (Freeze section 2).

The facade runs one RG1 unit or one RG10 bundle; those operations arrive with the run
command (Day 6) and RG10 evaluation. Day 5 adds what ``tdea research validate`` needs
from below the aggregator: check_url() applies FR-02 to a run file's repository URL
through the git_handler's UrlValidator, so the aggregator never imports git_handler
(NFR-14). The check is the FR-02 pattern only: it opens no connection, makes no request
and clones nothing (FR-48: validate has no side effects).
"""

from __future__ import annotations

from dataclasses import dataclass

from tdea.git_handler.errors import TdeaError
from tdea.git_handler.url_validator import UrlValidator


@dataclass(frozen=True)
class CheckedUrl:
    """A repository URL that passed FR-02, with the repository it names."""

    url: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Return owner/name."""
        return f"{self.owner}/{self.name}"


class UrlRejected(Exception):
    """A repository URL failed FR-02; carries the three FR-05 parts."""

    def __init__(self, error_type: str, cause: str, action: str) -> None:
        """Keep the three parts of the git_handler's error, never raw exception text."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action


class ResearchEngine:
    """Facade of the research analyzer; Day 5 offers the FR-02 check."""

    def check_url(self, url: object) -> CheckedUrl:
        """Return the repository a valid URL names, or raise UrlRejected (FR-02)."""
        try:
            repository = UrlValidator.parse(url)
        except TdeaError as exc:
            raise UrlRejected(exc.error_type, exc.cause, exc.action) from None
        return CheckedUrl(str(url), repository.owner, repository.name)
    