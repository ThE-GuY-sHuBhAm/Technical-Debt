"""Child process for TC-142: one real analysis of a public GitHub repository.

Usage: product_live_target.py <workspace> <github URL> <sampling interval N>. The
analysis uses TDEA's own engine with no stand-ins: the real FR-03 request to
api.github.com and the real clone from github.com. The test runs this script under
strace, so that every network connection of it and its child processes (git, Pylint)
is recorded. It prints the final status and exits 0 only when the analysis completed.
connections() reads the destinations from that strace log.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tdea.aggregator.analysis_service import AnalysisService
from tdea.aggregator.product_store import ProductStore
from tdea.analyzer.product_engine import ProductEngine

ADDRESS = re.compile(r'sa_family=(AF_INET6?)\b.*?_port=htons\((\d+)\).*?'
                     r'(?:inet_addr\("([^"]+)"\)|inet_pton\(AF_INET6, "([^"]+)")')


def connections(trace: str) -> set[tuple[str, int]]:
    """Return the (address, port) of every IPv4 or IPv6 connect() in an strace log."""
    found = set()
    for line in trace.splitlines():
        if "connect(" not in line:
            continue
        match = ADDRESS.search(line)
        if match:
            found.add((match.group(3) or match.group(4), int(match.group(2))))
    return found


def main(workspace: str, url: str, sampling_n: str) -> int:
    """Analyse ``url`` into ``workspace``; return 0 when the analysis is Complete."""
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    service = AnalysisService(ProductStore(root / "tdea.db"), ProductEngine(temp_root=root),
                              lock_directory=root / "lock")
    submission = service.submit(url, sampling_n=int(sampling_n))
    if not service.wait(submission.repository_id, timeout=1500):
        print("timeout", flush=True)
        return 1
    row = service.store.repository(submission.repository_id)
    print(row.status, row.commits_analysed, row.commits_total, flush=True)
    return 0 if row.status == "Complete" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
