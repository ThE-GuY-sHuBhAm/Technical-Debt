"""Test-wide guards: the in-process network guard (NFR-20) and the canary token (NFR-19).

Network guard. Every test runs with name resolution and socket connections checked in
process. A test may reach loopback addresses (in-process listeners) and nothing else.
A test marked ``github_network`` may also reach github.com and api.github.com, the
only hosts TDEA may contact. Anything else raises NetworkGuardError, which is not an
OSError, so code under test cannot mistake it for an ordinary network failure. In
unmarked tests urllib also sees no proxy settings, so a proxy on a loopback address
cannot carry traffic out. git and Pylint subprocesses are outside this guard; the
Day 13 network capture covers them.

Canary token. ``canary_token`` is a fake Personal Access Token for tests that pass a
token through the system. ``find_canary`` lists the files under given directories that
contain it; every such list must be empty.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

GITHUB_HOSTS = frozenset({"github.com", "api.github.com"})
CANARY_TOKEN = "ghp_TDEACANARY" + "7" * 26


class NetworkGuardError(RuntimeError):
    """A test tried to reach a host the network guard does not allow."""


def _is_loopback(host: str) -> bool:
    """Return True for loopback addresses and the name localhost."""
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


class NetworkGuard:
    """The hosts and addresses one test may contact."""

    def __init__(self, github_allowed: bool) -> None:
        """Allow loopback always, and the GitHub hosts only if ``github_allowed``."""
        self.github_allowed = github_allowed
        self._github_addresses: set[str] = set()

    def allows_host(self, host: str) -> bool:
        """Return True if the guard lets this test resolve or contact ``host``."""
        if _is_loopback(host):
            return True
        return self.github_allowed and host.lower().rstrip(".") in GITHUB_HOSTS

    def allows_address(self, address: Any) -> bool:
        """Return True if the guard lets this test connect to a socket ``address``."""
        if not isinstance(address, tuple):  # AF_UNIX paths are local
            return True
        host = str(address[0])
        return _is_loopback(host) or host in self._github_addresses

    def wrap_getaddrinfo(self, real: Callable[..., Any]) -> Callable[..., Any]:
        """Return a getaddrinfo that refuses disallowed hosts and records GitHub addresses."""

        def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if host is None:  # a passive (bind) lookup names no remote host
                return real(host, *args, **kwargs)
            name = host.decode() if isinstance(host, bytes) else str(host)
            if not self.allows_host(name):
                raise NetworkGuardError(f"test tried to resolve {name!r}")
            results = real(host, *args, **kwargs)
            if not _is_loopback(name):
                self._github_addresses.update(str(entry[4][0]) for entry in results)
            return results

        return getaddrinfo

    def wrap_connect(self, real: Callable[..., Any]) -> Callable[..., Any]:
        """Return a socket connect method that refuses disallowed addresses."""

        def connect(sock: socket.socket, address: Any) -> Any:
            if not self.allows_address(address):
                raise NetworkGuardError(f"test tried to connect to {address!r}")
            return real(sock, address)

        return connect


def _no_proxies() -> dict[str, str]:
    """Stand in for urllib's proxy discovery (environment, registry, macOS settings)."""
    return {}


def pytest_configure(config: pytest.Config) -> None:
    """Register the marker that lets a test reach the GitHub hosts."""
    config.addinivalue_line(
        "markers", "github_network: the test may reach github.com and api.github.com"
    )


@pytest.fixture(autouse=True)
def network_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NetworkGuard]:
    """Apply the network guard to every test and return it."""
    guard = NetworkGuard(request.node.get_closest_marker("github_network") is not None)
    monkeypatch.setattr(socket, "getaddrinfo", guard.wrap_getaddrinfo(socket.getaddrinfo))
    monkeypatch.setattr(socket.socket, "connect", guard.wrap_connect(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex",
                        guard.wrap_connect(socket.socket.connect_ex))
    if not guard.github_allowed:
        monkeypatch.setattr(urllib.request, "getproxies", _no_proxies)
    yield guard


@pytest.fixture
def canary_token() -> str:
    """Return the fake Personal Access Token that must never be stored."""
    return CANARY_TOKEN


@pytest.fixture
def find_canary() -> Callable[..., list[Path]]:
    """Return a function listing the files under given directories that hold the canary."""
    needle = CANARY_TOKEN.encode()

    def find(*roots: Path) -> list[Path]:
        found = []
        for root in roots:
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in filenames:
                    path = Path(dirpath) / name
                    try:
                        if needle in path.read_bytes():
                            found.append(path)
                    except OSError:
                        continue
        return sorted(found)

    return find