"""Fail-closed stops of the disappearance step (FR-56; Task 7 Day 7 decisions).

When FR-56 cannot be applied without a guess, the unit stops instead (decision R9,
option B, and readings R3, R5 and P6): an unsupported module-level classification
(CL-17 open), an item scope that cannot be located (R3), a commit whose provenance is
unknown (never read as non-traced, Task 6), a version that cannot be read, or two
changes of one followed path in one commit. The stop is raised before the unit's
transaction starts, so nothing of the unit is stored; earlier units stay. The aggregator
reports it through Day 6's existing unresolved-code path (the three FR-05 parts, the
undecided note and placeholder status 1); no exit code is mapped for these stops.

Messages hold SHAs, paths and line numbers only, never code text, scope names or
identities (Freeze, privacy: logs and console hold SHAs, paths and counts).
"""

from __future__ import annotations


class ClassificationStopped(Exception):
    """FR-56 cannot be applied to an item without guessing; carries the FR-05 parts."""

    def __init__(self, error_type: str, cause: str, action: str, category: str) -> None:
        """Keep the three parts and the failure category the exit-code note names."""
        self.error_type = error_type
        self.cause = cause
        self.action = action
        self.category = category
        super().__init__(f"{error_type}: {cause} {action}")


NOT_STORED = "The unit is not stored; earlier units are kept."


def module_level_undecided(path: str, line: int, c1_sha: str) -> ClassificationStopped:
    """Return the CL-17 stop for a module-level item that modes a, b and f do not settle."""
    return ClassificationStopped(
        "Disappearance mode undecided",
        f"The module-level item on line {line} of {path} disappears at commit {c1_sha}. "
        "D-14 makes module-level items skip modes (c) and (d), mode (e) needs a counterpart "
        "similarity they lack, and RE-11 expects a local fix: that conflict is open as "
        "CL-17, so no mode is guessed.",
        f"{NOT_STORED} Runs over such items wait for the CL-17 decision (G2, 22 Oct 2026).",
        "unsupported module-level classification (CL-17 open)")


def scope_unlocatable(path: str, line: int, c1_sha: str) -> ClassificationStopped:
    """Return the stop for an item whose scope is not one class, function or lambda."""
    return ClassificationStopped(
        "Item scope not located",
        f"The enclosing scope of the item on line {line} of {path}, in the version before "
        f"commit {c1_sha}, matches no single class, function or lambda of that version "
        "(none or several do), so its counterpart (D-1) cannot be determined.",
        f"{NOT_STORED} Report the case: resolving repeated scope names and lambdas is the "
        "provisional reading R3, open for the RM side.",
        "unlocatable item scope")


def provenance_unknown(sha: str) -> ClassificationStopped:
    """Return the stop for a commit whose trace status is unknown."""
    return ClassificationStopped(
        "Provenance unknown",
        f"Commit {sha} has no known trace status, and an unknown status is never read as "
        "non-traced, so the history, the resolver and the base rate cannot be recorded.",
        f"{NOT_STORED} Every commit must be classified before its unit runs (FR-54).",
        "unknown provenance")


def version_unreadable(path: str, c1_sha: str, before: bool) -> ClassificationStopped:
    """Return the stop for a linted version that cannot be parsed or tokenised again."""
    where = "in the version before" if before else "at"
    return ClassificationStopped(
        "File version unreadable",
        f"{path} {where} commit {c1_sha} linted, but cannot be parsed or tokenised for the "
        "counterpart (D-1, D-2).",
        f"{NOT_STORED} Report the file version.",
        "unreadable file version")


def change_inconsistent(sha: str, path: str) -> ClassificationStopped:
    """Return the stop for a commit that lists more than one change of a followed path."""
    return ClassificationStopped(
        "Inconsistent file change",
        f"Commit {sha} lists more than one change of {path}, so the file cannot be followed.",
        f"{NOT_STORED} Report the commit.",
        "inconsistent file change")
