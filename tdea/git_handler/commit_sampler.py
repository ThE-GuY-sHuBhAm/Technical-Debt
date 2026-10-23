"""FR-08: which commits a product analysis looks at (sprint 23 Oct).

A product analysis takes the commits reachable from HEAD (FR-07) oldest first, which is
the order of their sequence numbers. With 100 commits or fewer every commit is analysed.
With more than 100, every N-th commit is taken (default N = 2, minimum N = 1), counting
from the first, and the first commit and the HEAD commit are always included,
whatever N is. Research runs select commits by FR-48 and FR-49, never through this.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

DEFAULT_N = 2
SAMPLING_THRESHOLD = 100

Item = TypeVar("Item")


def check_n(sampling_n: object) -> int:
    """Return ``sampling_n`` as N, or raise ValueError when it is not a whole number >= 1."""
    if isinstance(sampling_n, bool) or not isinstance(sampling_n, int) or sampling_n < 1:
        raise ValueError("the sampling interval N must be a whole number of at least 1")
    return sampling_n


def sample(commits: Sequence[Item], sampling_n: int = DEFAULT_N) -> list[Item]:
    """Return the commits to analyse from ``commits`` (oldest first), in the same order."""
    step = check_n(sampling_n)
    if len(commits) <= SAMPLING_THRESHOLD:
        return list(commits)
    indices = set(range(0, len(commits), step)) | {len(commits) - 1}
    return [commits[index] for index in sorted(indices)]
