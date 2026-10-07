"""FR-02 URL validation on the inputs of TC-002 to TC-007 (unit level).

The workbook cases themselves are browser cases run later; these tests check the
validator they rely on. Decision 5 (execution plan, Day 1): http:// URLs pass FR-02,
but every address TDEA builds from them uses HTTPS.
"""

from __future__ import annotations

import pytest

from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.url_validator import RepositoryRef, UrlValidator


def test_pattern_is_the_srs_pattern() -> None:
    """The pattern is FR-02's, character for character."""
    assert UrlValidator.PATTERN == r"^https?://github\.com/[\w.-]+/[\w.-]+/?$"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/psf/requests",  # TC-002
        "https://github.com/psf/requests/",  # TC-003
        "http://github.com/psf/requests",  # FR-02 allows http
        "https://github.com/my.org-1/repo_name.v2",
    ],
)
def test_valid_urls_are_accepted(url: str) -> None:
    """URLs matching the pattern parse into owner and repository."""
    assert UrlValidator.is_valid(url)
    ref = UrlValidator.parse(url)
    assert ref.full_name == url.split("github.com/", 1)[1].rstrip("/")


def test_http_input_is_only_ever_used_over_https() -> None:
    """An http:// URL yields API and clone addresses that use HTTPS."""
    ref = UrlValidator.parse("http://github.com/psf/requests")
    assert ref == RepositoryRef("psf", "requests")
    assert ref.api_url == "https://api.github.com/repos/psf/requests"
    assert ref.clone_url == "https://github.com/psf/requests"


@pytest.mark.parametrize(
    ("url", "cause_fragment"),
    [
        ("https://gitlab.com/gitlab-org/gitlab", "not a github.com address"),  # TC-004
        ("github.com/psf/requests", "does not start with https://github.com/"),  # TC-005
        ("https://github.com/psf", "<owner>/<repo>"),  # TC-006
        ("https://github.com/psf/requests/tree/main", "<owner>/<repo>"),  # TC-007
        ("", "No repository URL"),
        ("https://github.com/psf/requests\n", "<owner>/<repo>"),
        ("https://github.com/psf/requests?tab=readme", "<owner>/<repo>"),
        ("ftp://github.com/psf/requests", "does not start with https://github.com/"),
    ],
)
def test_invalid_urls_are_rejected_with_a_structured_error(
    url: str, cause_fragment: str
) -> None:
    """Non-matching input raises a three-part INVALID_URL error naming the problem."""
    assert not UrlValidator.is_valid(url)
    with pytest.raises(TdeaError) as caught:
        UrlValidator.parse(url)
    error = caught.value
    assert error.code is ErrorCode.INVALID_URL
    assert error.error_type == "Invalid repository URL"
    assert cause_fragment in error.cause
    assert "https://github.com/<owner>/<repo>" in error.action


def test_non_string_input_is_rejected() -> None:
    """Anything that is not a string is rejected, not parsed."""
    assert not UrlValidator.is_valid(None)
    with pytest.raises(TdeaError):
        UrlValidator.parse(None)