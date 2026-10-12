"""The bulk/tooling signals of FR-56 mode (a): a1 configuration, a2 sweep, a3 message.

a1 ("c1 changes a lint or formatter configuration") and a3 ("has a formatter or lint
commit message") need the D-4 bulk lists, which are CL-03 lists. Version 0.1 of both is
incomplete and states no matching rule (rule_lists.py), so Cl03BulkRules refuses with
BulkRulesUnresolved whenever it is asked; nothing about the lists is decided here. The
two lists are part of the CL-03 gate (Task 7 Day 7 decision), so a real run is refused
before it starts, and a refusal during a unit is CL-03's refusal (exit 3). a3 is given
c1's stored message, the rewritten one: raw messages are not kept once FR-54 has run
(NFR-27), which the CL-03 matching rule will have to take into account.

a2 follows D-13 with the formatting-only lines of D-3 (signals.py) and reading R1 (I1,
approved provisionally): a2 holds when more than 20 of c1's changed files each have over
90% of their changed lines formatting-only. Other changes in c1 do not count against it.
Only .py files can qualify (D-3's tokens are Python tokens); a file's changed lines are
the removed plus the added lines of its hunks (FR-50, Git's numbering); a binary change
or a change without changed lines (a pure rename) never qualifies; a renamed file with
edits is judged on those edits; "over 90%" is tested exactly (10 x formatting-only >
9 x changed).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from tdea.analyzer.research.rg1.defaults import SWEEP_FILES, SWEEP_SHARE
from tdea.analyzer.research.rg1.provenance import ListVersion
from tdea.analyzer.research.rg1.signals import formatting_only_lines
from tdea.analyzer.research.rule_lists import (BULK_CONFIG_FILES, BULK_MESSAGE_PATTERNS,
                                               RuleList)
from tdea.git_handler.file_changes import FileChange

PYTHON_SUFFIX = ".py"


class BulkRulesUnresolved(NotImplementedError):
    """Raised instead of matching while CL-03's D-4 bulk lists and rules are undefined."""


class BulkRules(Protocol):
    """FR-56 (a1) and (a3): CL-03's D-4 bulk lists (Cl03BulkRules, or a test double)."""

    def config_changed(self, changes: Sequence[FileChange]) -> bool:
        """Return True if c1 changes a lint or formatter configuration (a1)."""

    def message_matches(self, message: str) -> bool:
        """Return True if c1's message is a formatter or lint message (a3)."""

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the versions of the lists used."""


class Cl03BulkRules:
    """The CL-03 v0.1 bulk lists: recorded and gated, but never matched while incomplete."""

    reason = ("CL-03 v0.1 does not define the D-4 bulk lists in full or how their entries "
              "match (FR-56 a1, a3), so no commit can be judged a configuration or "
              "formatter change with authoritative rules.")

    def __init__(self, config_files: RuleList = BULK_CONFIG_FILES,
                 messages: RuleList = BULK_MESSAGE_PATTERNS) -> None:
        """Use the given versioned lists."""
        self._lists = (config_files, messages)

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the version, completeness and content hash of both lists."""
        return tuple(ListVersion(rules.name, rules.version, rules.complete,
                                 rules.content_hash()) for rules in self._lists)

    def config_changed(self, changes: Sequence[FileChange]) -> bool:
        """Refuse: the a1 list and its matching rules are CL-03's, still undefined."""
        raise BulkRulesUnresolved(self.reason)

    def message_matches(self, message: str) -> bool:
        """Refuse: the a3 list and its matching rules are CL-03's, still undefined."""
        raise BulkRulesUnresolved(self.reason)


def formatting_sweep(changes: Sequence[FileChange],
                     read_blob: Callable[[str], bytes]) -> bool:
    """Return a2: more than 20 changed .py files each over 90% formatting-only (D-13, R1)."""
    qualifying = 0
    for change in changes:
        side = change.new if change.new is not None else change.old
        if side is None or not side.path.endswith(PYTHON_SUFFIX) or change.binary \
                or not change.hunks:
            continue
        before = read_blob(change.old.blob) if change.old is not None else None
        after = read_blob(change.new.blob) if change.new is not None else None
        formatting, changed = formatting_only_lines(change, before, after)
        if changed and formatting > SWEEP_SHARE * changed:
            qualifying += 1
    return qualifying > SWEEP_FILES
