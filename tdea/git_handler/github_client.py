"""FR-03 accessibility check and FR-04 optional token, over the GitHub REST API (v3).

One request, GET /repos/{owner}/{repo}, sent over HTTPS with TLS 1.2 or later (SRS 4.3,
4.4) through the standard library's urllib. The repository must be public and
non-empty before anything is cloned. Emptiness is read from the response's ``size``
field, GitHub's approximate repository size in KB, where 0 means empty (execution plan,
Task 3 decision 2).

The token is transient (FR-04, NFR-19): it is a parameter of one call, goes only into
that request's Authorization header, and is never stored, logged or put in a message.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tdea import __version__
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.url_validator import RepositoryRef

DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class RepositoryInfo:
    """What the accessibility check learned about a public, non-empty repository."""

    full_name: str
    default_branch: str
    size: int


class GitHubClient:
    """Checks that a repository is public and non-empty before it is cloned (FR-03)."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        urlopen: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        """Set the request timeout; ``urlopen`` is replaceable for tests."""
        self._timeout = timeout
        self._urlopen = urlopen
        self._tls = ssl.create_default_context()
        self._tls.minimum_version = ssl.TLSVersion.TLSv1_2

    def check_repository(
        self, repository: RepositoryRef, token: str | None = None
    ) -> RepositoryInfo:
        """Return the repository's details, or raise TdeaError if it cannot be analysed."""
        request = urllib.request.Request(
            repository.api_url, headers=_headers(token), method="GET"
        )
        try:
            with self._urlopen(request, timeout=self._timeout, context=self._tls) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            status, headers = exc.code, exc.headers
            exc.close()
            raise _http_failure(repository, status, headers) from exc
        except OSError as exc:  # URLError, timeouts and connection errors
            raise TdeaError(
                ErrorCode.NETWORK,
                "GitHub unreachable",
                "The connection to api.github.com failed or timed out.",
                "Check the internet connection and try again.",
            ) from exc
        return _repository_info(repository, body)


def _headers(token: str | None) -> dict[str, str]:
    """Return the request headers; the token, if any, goes only into Authorization."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"tdea/{__version__}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_failure(
    repository: RepositoryRef, status: int, headers: Mapping[str, str] | None
) -> TdeaError:
    """Turn an HTTP error status into a structured error (FR-03, FR-05)."""
    remaining = headers.get("X-RateLimit-Remaining") if headers is not None else None
    if status == 404:
        return TdeaError(
            ErrorCode.NOT_FOUND,
            "Repository not found",
            f"GitHub has no public repository {repository.full_name}; "
            "it may not exist, or it may be private.",
            "Check the owner and repository name, and that the repository is public.",
        )
    if status == 401:
        return TdeaError(
            ErrorCode.AUTH_FAILED,
            "Authentication failed",
            "GitHub did not accept the Personal Access Token.",
            "Check the token, or leave the token field empty to continue without one.",
        )
    if status == 429 or (status == 403 and remaining == "0"):
        return TdeaError(
            ErrorCode.RATE_LIMITED,
            "GitHub rate limit reached",
            "GitHub refused the request because the API rate limit is used up.",
            "Wait for the limit to reset, or supply a Personal Access Token.",
        )
    if status == 403:
        return TdeaError(
            ErrorCode.NOT_ACCESSIBLE,
            "Repository not accessible",
            f"GitHub refused access to {repository.full_name}.",
            "Use a public repository, or check the Personal Access Token.",
        )
    return _github_error(f"GitHub answered with HTTP status {status}.")


def _repository_info(repository: RepositoryRef, body: bytes) -> RepositoryInfo:
    """Check a 200 response: the repository must be public and non-empty."""
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise _github_error("GitHub's answer could not be read.")
    if data.get("private") is True:
        raise TdeaError(
            ErrorCode.PRIVATE_REPOSITORY,
            "Repository not accessible",
            f"{repository.full_name} is private; TDEA analyses public repositories only.",
            "Use a public repository.",
        )
    size = data.get("size")
    if data.get("private") is not False or not isinstance(size, int) or isinstance(size, bool):
        raise _github_error("GitHub's answer lacks the repository's visibility or size.")
    if size == 0:
        raise TdeaError(
            ErrorCode.EMPTY_REPOSITORY,
            "Empty repository",
            f"{repository.full_name} has no commits to analyse.",
            "Choose a repository that has at least one commit.",
        )
    return RepositoryInfo(
        full_name=str(data.get("full_name") or repository.full_name),
        default_branch=str(data.get("default_branch") or ""),
        size=size,
    )


def _github_error(cause: str) -> TdeaError:
    """Return the error for an answer from GitHub that TDEA cannot use."""
    return TdeaError(
        ErrorCode.GITHUB_ERROR,
        "Unexpected answer from GitHub",
        cause,
        "Try again later.",
    )
