"""The strace reading behind TC-142 (tests/product/test_github_live.py), run everywhere."""

from __future__ import annotations

from product_live_target import connections


def test_connections_are_read_from_strace_lines() -> None:
    """IPv4 and IPv6 destinations are read, unfinished calls included; Unix sockets are not."""
    trace = (
        '101 connect(5, {sa_family=AF_INET, sin_port=htons(443), '
        'sin_addr=inet_addr("140.82.112.3")}, 16) = 0\n'
        '102 connect(7, {sa_family=AF_INET6, sin6_port=htons(443), sin6_flowinfo=htonl(0), '
        'inet_pton(AF_INET6, "2606:50c0:8000::153", &sin6_addr), sin6_scope_id=0}, 28 '
        '<unfinished ...>\n'
        '103 connect(3, {sa_family=AF_UNIX, sun_path="/run/systemd/resolve/io.systemd.'
        'Resolve"}, 42) = 0\n'
        '104 connect(4, {sa_family=AF_INET, sin_port=htons(53), '
        'sin_addr=inet_addr("127.0.0.53")}, 16) = 0\n'
        '105 sendto(4, "x", 1, 0, NULL, 0) = 1\n')
    assert connections(trace) == {("140.82.112.3", 443), ("2606:50c0:8000::153", 443),
                                  ("127.0.0.53", 53)}
