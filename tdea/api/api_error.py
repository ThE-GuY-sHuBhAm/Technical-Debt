"""ApiError: the one structured error shape of the api layer (FR-05, NFR-11).

Every user-facing error states (a) what happened, (b) its probable cause and (c) the
corrective action, and never shows raw exception text. The web API and the research
command line share this class. Lower layers raise their own structured errors (the
git_handler's TdeaError, the aggregator's run-file problems, the analyzer's environment
refusal), which all carry the same three parts; ApiError wraps any of them by those
attributes, so the api layer imports nothing below the aggregator (NFR-14).

``status`` is the HTTP status for the web API. The command line has no HTTP status and
leaves it None; its exit codes are chosen by the command line itself (FR-48).
"""

from __future__ import annotations


class ApiError(Exception):
    """A failure stated as error type, probable cause and corrective action."""

    def __init__(self, error_type: str, cause: str, action: str,
                 status: int | None = None) -> None:
        """Keep the three parts, each required (NFR-11), and the optional HTTP status."""
        for name, value in (("error_type", error_type), ("cause", cause), ("action", action)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"an ApiError needs a non-empty {name}")
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action
        self.status = status
        self.field: str | None = None  # the request field the error is about, if one

    @classmethod
    def wrap(cls, error: object, status: int | None = None) -> ApiError:
        """Return the ApiError for a structured error from a lower layer.

        ``error`` must carry ``error_type``, ``cause`` and ``action``; an object without
        them is a programming error and is refused rather than shown raw (FR-05).
        """
        try:
            parts = (error.error_type, error.cause, error.action)  # type: ignore[attr-defined]
        except AttributeError as exc:
            raise TypeError("only structured errors with three parts can be wrapped") from exc
        return cls(*parts, status=status)

    def lines(self) -> tuple[str, str, str]:
        """Return the three parts as the lines the command line prints."""
        return (f"Error: {self.error_type}", f"Cause: {self.cause}", f"Action: {self.action}")
    