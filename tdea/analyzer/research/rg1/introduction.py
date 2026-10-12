"""IntroductionDetector: which debt items a commit introduces (FR-53).

Commit c introduces an item when (1) the item is new at c, (2) its line is on the added
side of c's hunks, (3) c is selected, (4) c touches at least one package .py file and
(5) c is not a bot commit (FR-53). New means left over after D-12 matching (ItemMatcher).
A file that fails to lint in either version introduces nothing at c and is counted
(FR-51 e, FR-53); a file added by c has an empty parent version.

Conditions (4) and (5) need the commit's trace status and the package filter, and both
depend on CL-03, whose matching rules are not defined yet. The detector therefore asks
for them only when an introduction depends on them, and never guesses:

* c not selected: nothing is introduced, and nothing else is consulted.
* no new occurrence on an added line in any changed .py file: nothing is introduced; the
  trace status and the package filter are not consulted.
* otherwise the trace status decides first: None (not classified, Task 6 decision 4)
  refuses with MatchingRulesUnresolved, never read as non-traced; a bot commit introduces
  nothing, with no package question. For any other status, the package filter is asked
  about each file with candidates; it refuses (FilterRulesUnresolved) while CL-03 is
  incomplete.

Files are reported, never silently dropped. ``failed_files`` lists each changed .py file
that failed to lint in either version; whether such a failure counts for the unit (only
package files are linted in a run, RE-05) is decided where the package filter is applied
to the whole unit, so no CL-03 question is asked here just to count. ``binary_files``
lists each changed .py file that Git diffs as binary: it has no hunks, so no flagged line
can be placed on an added side, and nothing is introduced from it.

Only .py files can be package code (glossary); every other file is skipped. A deleted
file has no version at c and introduces nothing. Only changed files are examined: an
unchanged file has no added lines at c.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from tdea.analyzer.research.profile import OK, LintResult
from tdea.analyzer.research.rg1.matching import (ItemKey, ItemMatcher, Occurrence,
                                                 occurrences, on_added_side)
from tdea.analyzer.research.rg1.package_filter import PYTHON_SUFFIX
from tdea.analyzer.research.rg1.provenance import MatchingRulesUnresolved, TraceStatus
from tdea.git_handler.file_changes import FileChange
from tdea.git_handler.research_commit import ResearchCommit


class PackageDecider(Protocol):
    """Says whether a path is package code (PackageFilter, FR-51 d)."""

    def is_package(self, path: str) -> bool:
        """Return True for package code; may refuse while CL-03 is incomplete."""


@dataclass(frozen=True)
class FileVersions:
    """One changed file of c: its change, and the lint and content of both versions."""

    change: FileChange
    before: LintResult | None  # None: the file has no parent version (added by c)
    after: LintResult | None  # None: the file has no version at c (deleted by c)
    before_content: bytes = b""
    after_content: bytes = b""

    def __post_init__(self) -> None:
        """For a .py file, require lint results exactly for the versions that exist."""
        side = self.change.new if self.change.new is not None else self.change.old
        if side is None or not side.path.endswith(PYTHON_SUFFIX):
            return
        if (self.before is None) != (self.change.old is None):
            raise ValueError("a .py file needs a parent lint result exactly when it has a "
                             "parent version")
        if (self.after is None) != (self.change.new is None):
            raise ValueError("a .py file needs a lint result at c exactly when it exists at c")

    @property
    def path(self) -> str | None:
        """Return the file's path at c, or None if c deletes it."""
        return self.change.new.path if self.change.new is not None else None

    @property
    def failed(self) -> bool:
        """Return True if either version failed to lint (FR-51 e)."""
        return any(result is not None and result.status != OK
                   for result in (self.before, self.after))


@dataclass(frozen=True)
class Introduction:
    """One debt item introduced at c (FR-53 outputs)."""

    commit_sha: str
    is_merge: bool
    introduced_at: datetime
    path: str
    blob: str
    key: ItemKey
    message_id: str
    category: str
    message: str
    line: int
    occurrence: int

    @property
    def symbol(self) -> str:
        """Return the rule symbol."""
        return self.key.symbol

    @property
    def scope(self) -> str:
        """Return the enclosing-scope name."""
        return self.key.scope

    @property
    def normalised_text(self) -> str:
        """Return the normalised flagged-line text."""
        return self.key.text


@dataclass(frozen=True)
class CommitIntroductions:
    """What c introduces, and the changed .py files that could not be examined."""

    introductions: tuple[Introduction, ...] = ()
    failed_files: tuple[str, ...] = ()  # failed to lint in either version (FR-51 e)
    binary_files: tuple[str, ...] = ()  # diffed as binary: no hunks to place lines on


class IntroductionDetector:
    """Applies FR-53's introduction rule to one commit."""

    def __init__(self, package_filter: PackageDecider,
                 matcher: ItemMatcher | None = None) -> None:
        """Use ``package_filter`` for condition (4) and ``matcher`` for D-12 matching."""
        self._package_filter = package_filter
        self._matcher = matcher if matcher is not None else ItemMatcher()

    def candidates(self, versions: FileVersions) -> tuple[Occurrence, ...]:
        """Return the occurrences new at c on added lines: conditions (1) and (2).

        A file that fails to lint in either version, or that c deletes, has none.
        """
        if versions.after is None or versions.failed or versions.change.binary:
            return ()
        before = (occurrences(versions.before.messages, versions.before_content)
                  if versions.before is not None else ())
        after = occurrences(versions.after.messages, versions.after_content)
        result = self._matcher.match(before, after, versions.change.hunks)
        return tuple(occurrence for occurrence in result.new
                     if on_added_side(occurrence.diff_line, versions.change.hunks))

    def detect(self, commit: ResearchCommit, selected: bool,
               trace_status: TraceStatus | None,
               files: Sequence[FileVersions]) -> CommitIntroductions:
        """Return what ``commit`` introduces, asking for CL-03 answers only when needed."""
        if not selected:
            return CommitIntroductions()
        found: list[tuple[FileVersions, tuple[Occurrence, ...]]] = []
        failed: list[str] = []
        binary: list[str] = []
        for versions in sorted(files, key=lambda item: item.path or ""):
            path = versions.path
            if path is None or not path.endswith(PYTHON_SUFFIX):
                continue
            if versions.failed:
                failed.append(path)
            elif versions.change.binary:
                binary.append(path)
            else:
                new = self.candidates(versions)
                if new:
                    found.append((versions, new))
        examined = CommitIntroductions((), tuple(failed), tuple(binary))
        if not found:
            return examined
        if trace_status is None:
            raise MatchingRulesUnresolved(
                "This commit adds warnings on added lines, but its trace status is not "
                "classified: CL-03 v0.1 defines no matching rule, so whether it is a bot "
                "commit cannot be decided (FR-53, FR-54).")
        if not isinstance(trace_status, TraceStatus):
            raise TypeError("trace_status must be a TraceStatus or None")
        if trace_status is TraceStatus.BOT:
            return examined
        return replace(examined, introductions=tuple(
            _introduction(commit, versions, occurrence)
            for versions, new in found if self._package_filter.is_package(versions.path)
            for occurrence in new))


def _introduction(commit: ResearchCommit, versions: FileVersions,
                  occurrence: Occurrence) -> Introduction:
    """Build the record of one introduced item."""
    new_side = versions.change.new
    message = occurrence.message
    return Introduction(
        commit_sha=commit.sha, is_merge=commit.is_merge, introduced_at=commit.committed_at,
        path=new_side.path, blob=new_side.blob, key=occurrence.key,
        message_id=message.message_id, category=message.category, message=message.message,
        line=message.line, occurrence=occurrence.index)
