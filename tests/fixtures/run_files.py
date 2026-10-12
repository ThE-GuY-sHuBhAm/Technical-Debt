"""Run-file samples for the validate tests (FR-48, SRS 4.5.2).

The sample is the RG1 schema example of SRS 4.5.2 with its placeholder repository
https://github.com/owner/name, so validation never refers to a real repository and needs
no network. Tests derive the bad run files of RE-01 step 3 from it.
"""

from __future__ import annotations

from collections.abc import Sequence


def schema_example(commits: Sequence[str] | None = None) -> str:
    """Return the RG1 schema example; ``commits`` limits its second window to those SHAs."""
    second = "{start: 2025-07-01, end: 2026-01-01}"
    if commits is not None:
        second = f"{{start: 2025-07-01, end: 2026-01-01, commits: [{', '.join(commits)}]}}"
    return (
        "kind: rg1\n"
        "store: research.db\n"
        "key_file: ~/.tdea/research.key\n"
        "seeds: {sampling: 20261005}\n"
        "w_months: 6\n"
        "tau: 0.7\n"
        "cutoff: null\n"
        "repositories:\n"
        "  - url: https://github.com/owner/name\n"
        "    head: null\n"
        "    windows:\n"
        "      - {start: 2025-01-01, end: 2025-07-01}\n"
        f"      - {second}\n"
    )
