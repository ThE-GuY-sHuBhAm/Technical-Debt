"""FR-03 accessibility check and FR-04 token handling, against mocked GitHub answers.

No test here reaches the network: the client gets a fake urlopen, and the one test that
uses the real urlopen shows that the network guard stops it. Covers the backend side of
TC-009 to TC-011 and TC-013 to TC-015, and decisions 2, 3 and 5 of the execution plan.
"""

from __future__ import annotations

import io
import json
import socket
import ssl
import urllib.error
from collections.abc import Callable
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.github_client import GitHubClient, RepositoryInfo
from tdea.git_handler.url_validator import UrlValidator

REPO = UrlValidator.parse("https://github.com/psf/requests")
PUBLIC = {"full_name": "psf/requests", "private": False, "size": 9876,
          "default_branch": "main"}


def fake_urlopen(outcome: Any) -> tuple[Callable[..., Any], list[dict[str, Any]]]:
    """Return a urlopen stand-in that records each call and answers with ``outcome``."""
    calls: list[dict[str, Any]] = []

    def urlopen(request: Any, timeout: float, context: ssl.SSLContext) -> io.BytesIO:
        calls.append({"request": request, "timeout": timeout, "context": context})
        if isinstance(outcome, BaseException):
            raise outcome
        body = outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode()
        return io.BytesIO(body)

    return urlopen, calls


def http_error(status: int, **headers: str) -> urllib.error.HTTPError:
    """Return the HTTPError urllib raises for ``status``."""
    message = Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return urllib.error.HTTPError(REPO.api_url, status, "error", message, io.BytesIO(b"{}"))


def check(outcome: Any, token: str | None = None) -> RepositoryInfo:
    """Run one accessibility check against a fake GitHub answer."""
    urlopen, _calls = fake_urlopen(outcome)
    return GitHubClient(urlopen=urlopen).check_repository(REPO, token)


def test_public_non_empty_repository_passes() -> None:
    """A 200 answer for a public repository with a size above 0 is accepted."""
    assert check(PUBLIC) == RepositoryInfo("psf/requests", "main", 9876)


def test_request_goes_to_the_frozen_endpoint_over_https() -> None:
    """One GET to /repos/{owner}/{repo} on api.github.com, TLS 1.2 or later."""
    urlopen, calls = fake_urlopen(PUBLIC)
    GitHubClient(urlopen=urlopen, timeout=7.5).check_repository(
        UrlValidator.parse("http://github.com/psf/requests")
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["request"].full_url == "https://api.github.com/repos/psf/requests"
    assert call["request"].get_method() == "GET"
    assert call["timeout"] == 7.5
    assert call["context"].minimum_version >= ssl.TLSVersion.TLSv1_2


def test_no_token_means_no_authorization_header() -> None:
    """Without a token the request is anonymous."""
    urlopen, calls = fake_urlopen(PUBLIC)
    GitHubClient(urlopen=urlopen).check_repository(REPO)
    assert calls[0]["request"].get_header("Authorization") is None


def test_token_is_sent_once_and_not_kept(canary_token: str) -> None:
    """The token goes into that request's Authorization header and nowhere else (TC-013)."""
    urlopen, calls = fake_urlopen(PUBLIC)
    client = GitHubClient(urlopen=urlopen)
    client.check_repository(REPO, canary_token)
    assert calls[0]["request"].get_header("Authorization") == f"Bearer {canary_token}"
    assert canary_token not in repr(vars(client))


@pytest.mark.parametrize(
    ("outcome", "code", "error_type"),
    [
        (http_error(404), ErrorCode.NOT_FOUND, "Repository not found"),  # TC-009, TC-010
        (http_error(403), ErrorCode.NOT_ACCESSIBLE, "Repository not accessible"),
        (http_error(403, X_RateLimit_Remaining="0"), ErrorCode.RATE_LIMITED,
         "GitHub rate limit reached"),
        (http_error(429), ErrorCode.RATE_LIMITED, "GitHub rate limit reached"),
        (http_error(500), ErrorCode.GITHUB_ERROR, "Unexpected answer from GitHub"),
        ({**PUBLIC, "private": True}, ErrorCode.PRIVATE_REPOSITORY,
         "Repository not accessible"),
        ({**PUBLIC, "size": 0}, ErrorCode.EMPTY_REPOSITORY, "Empty repository"),  # TC-011
        ({**PUBLIC, "size": None}, ErrorCode.GITHUB_ERROR, "Unexpected answer from GitHub"),
        ({"size": 10}, ErrorCode.GITHUB_ERROR, "Unexpected answer from GitHub"),
        (b"not json", ErrorCode.GITHUB_ERROR, "Unexpected answer from GitHub"),
        (urllib.error.URLError("down"), ErrorCode.NETWORK, "GitHub unreachable"),
        (socket.timeout("slow"), ErrorCode.NETWORK, "GitHub unreachable"),
    ],
)
def test_failures_are_structured(outcome: Any, code: ErrorCode, error_type: str) -> None:
    """Each failure is a three-part TdeaError of its own kind (FR-03, FR-05)."""
    with pytest.raises(TdeaError) as caught:
        check(outcome)
    error = caught.value
    assert (error.code, error.error_type) == (code, error_type)
    assert error.cause and error.action


def test_not_found_names_both_likely_causes() -> None:
    """GitHub answers 404 for missing and for private repositories alike."""
    with pytest.raises(TdeaError) as caught:
        check(http_error(404))
    assert "may not exist" in caught.value.cause
    assert "private" in caught.value.cause


def test_rejected_token_is_never_echoed(
    canary_token: str, tmp_path: Path, find_canary: Callable[..., list[Path]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 401 gives 'Authentication failed' without the token anywhere (TC-014, NFR-19)."""
    with pytest.raises(TdeaError) as caught:
        check(http_error(401), canary_token)
    error = caught.value
    assert error.code is ErrorCode.AUTH_FAILED
    assert error.error_type == "Authentication failed"
    texts = [str(error), error.error_type, error.cause, error.action, caplog.text]
    assert not any(canary_token in text for text in texts)
    assert not find_canary(tmp_path)


def test_real_urlopen_is_stopped_by_the_network_guard() -> None:
    """Unit tests get no network: the default client cannot reach GitHub here."""
    with pytest.raises(RuntimeError, match="tried to"):
        GitHubClient(timeout=2).check_repository(REPO)