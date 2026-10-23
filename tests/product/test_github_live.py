"""FR-03 against the real GitHub REST API: the genuine integration evidence (23 Oct).

Every other FR-03 test answers with a fake urlopen or StubClient. These tests send the
real GET /repos/{owner}/{repo} to api.github.com without a token, as `tdea serve` does
by default, so they need network access to GitHub. They run only when TDEA_LIVE_GITHUB
is 1: the CI job "live GitHub API (FR-03)" sets it on a GitHub-hosted runner. Where the
variable is unset they are reported as skipped, never as passed. A rate-limit answer
fails the test rather than skipping it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.product_store import ProductStore
from tdea.analyzer.product_engine import ProductEngine
from tdea.api.app import create_app
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.github_client import GitHubClient
from tdea.git_handler.url_validator import UrlValidator

pytestmark = [
    pytest.mark.github_network,
    pytest.mark.skipif(os.environ.get("TDEA_LIVE_GITHUB") != "1",
                       reason="live GitHub API test: set TDEA_LIVE_GITHUB=1 to run it"),
]

PUBLIC = "https://github.com/psf/requests"
MISSING = "https://github.com/psf/tdea-no-such-repository-2026"
BAD_TOKEN = "ghp_" + "0" * 36  # well-formed, never issued


def test_a_public_repository_passes_the_check() -> None:
    """FR-03, TC-009 backend: a public, non-empty repository is accepted."""
    info = GitHubClient().check_repository(UrlValidator.parse(PUBLIC))
    assert info.full_name.lower() == "psf/requests"
    assert info.size > 0
    assert info.default_branch


def test_a_missing_repository_is_not_found() -> None:
    """FR-03, TC-010 backend: GitHub's 404 becomes the not-found error."""
    with pytest.raises(TdeaError) as caught:
        GitHubClient().check_repository(UrlValidator.parse(MISSING))
    assert caught.value.code is ErrorCode.NOT_FOUND


def test_a_rejected_token_is_an_authentication_failure() -> None:
    """FR-04, TC-015 backend: a token GitHub does not accept gives the 401 error."""
    with pytest.raises(TdeaError) as caught:
        GitHubClient().check_repository(UrlValidator.parse(PUBLIC), token=BAD_TOKEN)
    assert caught.value.code is ErrorCode.AUTH_FAILED


def test_the_web_api_reports_a_missing_repository(tmp_path: Path) -> None:
    """FR-03, FR-05, NFR-07: the real check behind POST /api/analyses gives 422."""
    service = AnalysisService(ProductStore(tmp_path / "tdea.db"),
                              ProductEngine(temp_root=tmp_path), lock_directory=tmp_path / "lock")
    response = create_app(service).test_client().post("/api/analyses", json={"url": MISSING})
    assert response.status_code == 422
    assert set(response.json["error"]) == {"type", "cause", "action"}
