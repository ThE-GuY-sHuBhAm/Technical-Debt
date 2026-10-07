"""Self-tests for the network guard and the canary token in tests/conftest.py."""

from __future__ import annotations

import socket
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest


def test_unit_tests_cannot_resolve_any_external_host() -> None:
    """Without the marker, even the GitHub hosts cannot be resolved."""
    for host in ("example.com", "api.github.com", "github.com"):
        with pytest.raises(RuntimeError, match="tried to resolve"):
            socket.getaddrinfo(host, 443)


def test_external_address_literal_is_refused() -> None:
    """A connection to a non-loopback address fails before any packet is sent."""
    with pytest.raises(RuntimeError, match="tried to resolve"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="tried to connect"):
            sock.connect(("192.0.2.1", 443))


def test_guard_error_is_not_an_os_error() -> None:
    """Code that catches network errors (OSError) cannot swallow a guard refusal."""
    with pytest.raises(RuntimeError) as caught:
        socket.getaddrinfo("example.com", 443)
    assert not isinstance(caught.value, OSError)


def test_unit_tests_see_no_proxy() -> None:
    """A proxy, even on a loopback address, cannot carry a unit test's traffic out."""
    assert not urllib.request.getproxies()


def test_loopback_listener_is_allowed() -> None:
    """In-process listeners on the loopback address stay reachable."""
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass


@pytest.mark.github_network
def test_marker_allows_only_the_github_hosts(network_guard: object) -> None:
    """With the marker, github.com and api.github.com are allowed and nothing else."""
    assert network_guard.allows_host("api.github.com")
    assert network_guard.allows_host("github.com")
    assert not network_guard.allows_host("example.com")
    assert not network_guard.allows_host("api.github.com.example.net")
    with pytest.raises(RuntimeError, match="tried to resolve"):
        socket.getaddrinfo("example.com", 443)


def test_find_canary_reports_files_holding_the_token(
    tmp_path: Path, canary_token: str, find_canary: Callable[..., list[Path]]
) -> None:
    """The canary search finds a planted copy and nothing in clean files."""
    (tmp_path / "clean.txt").write_text("nothing here", encoding="utf-8")
    assert not find_canary(tmp_path)
    planted = tmp_path / "nested" / "log.txt"
    planted.parent.mkdir()
    planted.write_text(f"token={canary_token}", encoding="utf-8")
    assert find_canary(tmp_path) == [planted]