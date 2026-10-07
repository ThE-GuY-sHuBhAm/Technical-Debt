"""Structured failures of the git_handler layer.

Every failure the layer reports carries the three parts FR-05 asks of a user-facing
error: what happened (error_type), its probable cause and the corrective action. The
api layer's ApiError (FR-05, Day 5) wraps these; until then they are the structured
form. ``code`` keeps the kinds of failure apart for callers such as the research
command line, whose exit code for a failed accessibility check is still open (CL-19).
No message ever contains raw exception text or a token.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(Enum):
    """The kinds of failure the git_handler layer reports."""

    INVALID_URL = "invalid_url"
    NOT_FOUND = "not_found"
    NOT_ACCESSIBLE = "not_accessible"
    PRIVATE_REPOSITORY = "private_repository"
    EMPTY_REPOSITORY = "empty_repository"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    GITHUB_ERROR = "github_error"
    CLONE_FAILED = "clone_failed"
    HISTORY_FAILED = "history_failed"
    INVALID_SELECTION = "invalid_selection"


class TdeaError(Exception):
    """A failure stated as error type, probable cause and corrective action (FR-05)."""

    def __init__(self, code: ErrorCode, error_type: str, cause: str, action: str) -> None:
        """Store the three parts and the failure kind."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.code = code
        self.error_type = error_type
        self.cause = cause
        self.action = action