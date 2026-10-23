"""FR-03 against the real GitHub REST API: the genuine integration evidence (23 Oct).

Every other FR-03 test answers with a fake urlopen or StubClient. These tests send the
real GET /repos/{owner}/{repo} to api.github.com without a token, as `tdea serve` does
by default, so they need network access to GitHub. They run only when TDEA_LIVE_GITHUB
is 1: the CI job "live GitHub API (FR-03)" sets it on a GitHub-hosted runner. Where the
variable is unset they are reported as skipped, never as passed. A rate-limit answer
fails the test rather than skipping it.

TC-142 (NFR-20, data confinement) runs one full analysis of a small public repository in
a child process under ``strace -f -e trace=connect``. Every connection that TDEA, git and
Pylint open is recorded. Each non-loopback destination must lie in the address ranges
GitHub publishes for github.com, api.github.com and git (GET /meta), apart from the
configured DNS resolver on port 53. The workbook names a packet capture of an FX-S
analysis. FX-S is a generated local fixture, so a public repository stands in, and
strace replaces the packet capture: it sees only this analysis's processes, not other
traffic on the machine. Without strace the test is skipped, unless TDEA_TC142=1 (set
in CI), in which case it fails.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from product_live_target import connections

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
TC142_REPOSITORY = "https://github.com/pallets/itsdangerous"
TC142_SAMPLING = "20"  # about 35 of its commits: a full analysis that stays short
TARGET = Path(__file__).resolve().parents[1] / "fixtures" / "product_live_target.py"


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


def resolvers() -> set[str]:
    """Return the name servers configured in /etc/resolv.conf."""
    path = Path("/etc/resolv.conf")
    if not path.is_file():
        return set()
    return {line.split()[1] for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("nameserver") and len(line.split()) > 1}


def github_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Return the networks GitHub publishes for its web, API and git hosts (GET /meta)."""
    with urllib.request.urlopen("https://api.github.com/meta", timeout=30) as answer:
        meta = json.load(answer)
    return [ipaddress.ip_network(block) for key in ("web", "api", "git")
            for block in meta.get(key, [])]


def test_tc142_full_analysis_contacts_only_github(tmp_path: Path) -> None:
    """TC-142: a full real analysis connects only to GitHub's published addresses."""
    strace = shutil.which("strace")
    if strace is None:
        if os.environ.get("TDEA_TC142") == "1":
            pytest.fail("TC-142 needs strace, and TDEA_TC142=1 requires the test to run")
        pytest.skip("TC-142 needs strace (Linux) to record the analysis's connections")
    log = tmp_path / "connect.log"
    completed = subprocess.run(
        [strace, "-f", "-qq", "-e", "trace=connect", "-e", "signal=none", "-o", str(log),
         sys.executable, str(TARGET), str(tmp_path / "ws"), TC142_REPOSITORY,
         TC142_SAMPLING], capture_output=True, text=True, timeout=1800, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith("Complete"), completed.stdout
    destinations = connections(log.read_text(encoding="utf-8", errors="replace"))
    networks = github_networks()
    dns = resolvers()
    outside = sorted(
        (address, port) for address, port in destinations
        if not ipaddress.ip_address(address).is_loopback
        and not (port == 53 and address in dns)
        and not any(ipaddress.ip_address(address) in network for network in networks))
    reached = [(address, port) for address, port in destinations
               if any(ipaddress.ip_address(address) in network for network in networks)]
    print("destinations:", sorted(destinations))
    assert reached, "no connection to GitHub was recorded: the trace did not work"
    assert outside == [], outside
