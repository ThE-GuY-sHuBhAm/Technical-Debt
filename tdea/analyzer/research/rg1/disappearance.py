"""DisappearanceClassifier: the mode of a disappearance at c1, with its signals (FR-56).

For an item that disappears at c1 (FR-55), with V0 its file just before c1, the change
of c1 to that file and V1 the file at c1 (if any), the classifier computes every signal
and then one mode per tau in 0.5, 0.6, 0.7 and 0.8, in FR-56's order of precedence
(Task 7 Day 7, approved):

1. (a) bulk/tooling if a1, a2 or a3 holds (bulk.py);
2. otherwise (b) if c1 removes the file;
3. otherwise (f) if V1 fails to lint ("including a file that fails to lint at c1 when (a)
   and (b) do not apply");
4. otherwise, for a module-level item, the unit fails closed: D-14 skips (c) and (d), (e)
   needs a similarity module-level items lack, and RE-11 expects a local fix (CL-17 open;
   Day 7 decision R9, option B). No mode is guessed;
5. otherwise (c) if the scope has no counterpart (D-1);
6. otherwise (d) if the counterpart similarity is below tau (D-2);
7. otherwise (e) local fix if c1's hunks touch the warning's line exactly (D-5);
8. otherwise (f).

The local-fix flag is true exactly when the mode is (e); every mode carries unvalidated =
true when stored. Signals are computed whenever they can be and are null otherwise (Day 7
reading P3): the counterpart and similarity are null when V1 is missing or fails to lint
and for module-level items; the line touches are null for a binary change. A
non-module-level item whose scope cannot be located in V0 fails closed (R3).

Counterpart (D-1, reading R3: A1, B1, C1, approved provisionally; the repeated-name and
lambda rule stays open for the RM side): the item's scope is the one span of V0 with its
scope name that holds its line; the counterpart is the same-named scope of V1, the most
similar one when the name repeats (ties: the first in source order), stored as "same
name"; with no same-named scope, the most similar scope of the same kind with similarity
>= 0.8, stored as "most similar"; otherwise none (Day 7 reading P4 for the stored words).

Resolver (FR-56): c1's trace status and tool. Same-account flags: equal author-name keys
and equal author-e-mail keys of c and c1, null if either key is null (Freeze, privacy;
flags compare keys, Decision 2). An unknown trace status fails closed. unit_summaries()
gives one row per tau for a unit: its disappearances, local fixes and local-fix share,
null when there are no disappearances (reading P1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from tdea.analyzer.research.profile import OK, LintResult
from tdea.analyzer.research.rg1.base_rate import ChangeEntry
from tdea.analyzer.research.rg1.bulk import BulkRules, formatting_sweep
from tdea.analyzer.research.rg1.defaults import COUNTERPART_MIN_SIMILARITY, TAUS
from tdea.analyzer.research.rg1.fail_closed import (change_inconsistent,
                                                    module_level_undecided,
                                                    provenance_unknown, scope_unlocatable,
                                                    version_unreadable)
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import Occurrence, occurrences
from tdea.analyzer.research.rg1.provenance import Provenance, TraceStatus
from tdea.analyzer.research.rg1.scopes import (MODULE_LEVEL, ScopeSpan, locate,
                                               scope_spans)
from tdea.analyzer.research.rg1.signals import (LineTouch, SourceUnreadable, git_span,
                                                line_touch, python_tokens, scope_tokens,
                                                similarity)
from tdea.analyzer.research.rg1.tracking import Tracking
from tdea.git_handler.file_changes import FileChange

BULK, REMOVED, DELETED, REWRITTEN, LOCAL_FIX, ARTIFACT = "a", "b", "c", "d", "e", "f"
SAME_NAME = "same name"
MOST_SIMILAR = "most similar"


class ClassifierSource(Protocol):
    """What the classifier reads: blobs and the research-profile lint of a version."""

    def read_blob(self, blob: str) -> bytes:
        """Return a blob's exact bytes."""

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Return the research-profile lint result of one file version (FR-51)."""


class RecordedCommit(Protocol):
    """A recorded commit (CommitRecord): keys, rewritten message, provenance, changes."""

    sha: str
    author_name_key: str | None
    author_email_key: str | None
    message: str
    provenance: Provenance
    changes: Sequence[ChangeEntry]


@dataclass(frozen=True)
class Counterpart:
    """D-1's counterpart of an item's scope in V1, and its D-2 similarity."""

    method: str | None = None
    scope: str | None = None
    similarity: float | None = None


@dataclass(frozen=True)
class Disappearance:
    """One disappearance at c1: every FR-56 signal and the mode at each tau."""

    bulk_a1: bool
    bulk_a2: bool
    bulk_a3: bool
    file_removed: bool
    parse_failure_reason: str | None
    counterpart: Counterpart
    touch: LineTouch | None
    resolver_trace_status: str
    resolver_tool: str | None
    same_name: bool | None
    same_email: bool | None
    modes: tuple[tuple[float, str], ...]

    def mode_at(self, tau: float) -> str:
        """Return the mode at ``tau``."""
        return dict(self.modes)[tau]


@dataclass(frozen=True)
class UnitSummary:
    """A unit's disappearances, local fixes and local-fix share at one tau (FR-56)."""

    tau: float
    disappearances: int
    local_fixes: int
    local_fix_share: float | None


@dataclass(frozen=True)
class _Before:
    """The item in V0: the version's bytes, the occurrence and the item's scope span."""

    content: bytes
    item: Occurrence
    span: ScopeSpan | None  # None for a module-level item


@dataclass(frozen=True)
class _Evidence:
    """What the mode at each tau depends on, and what a CL-17 stop names."""

    bulk: bool
    removed: bool
    lint_failed: bool
    module_level: bool
    similarity: float | None
    exact_touch: bool | None
    path: str
    line: int
    c1_sha: str


class DisappearanceClassifier:
    """Applies FR-56 to the items of one repository's units."""

    def __init__(self, source: ClassifierSource, bulk: BulkRules,
                 commits: Mapping[str, RecordedCommit]) -> None:
        """Read through ``source``; judge a1 and a3 with ``bulk``; find commits by SHA."""
        self._source = source
        self._bulk = bulk
        self._commits = commits

    def classify(self, introduction: Introduction, tracking: Tracking) -> Disappearance:
        """Return the disappearance of an item whose tracking found c1."""
        if tracking.c1_sha is None or tracking.path_before_c1 is None \
                or tracking.blob_before_c1 is None or tracking.line_before_c1 is None:
            raise ValueError("only an item with c1 has a disappearance")
        c1 = self._commits[tracking.c1_sha]
        path = tracking.path_before_c1
        change = _change_of(c1, path)
        before = self._before(introduction, tracking, c1.sha)
        changes = tuple(entry.change for entry in c1.changes)
        bulk = (self._bulk.config_changed(changes),
                formatting_sweep(changes, self._source.read_blob),
                self._bulk.message_matches(c1.message))
        after, lint = self._after(change)
        failure = lint.failure_reason if lint is not None and lint.status != OK else None
        counterpart = Counterpart()
        if after is not None and change.new is not None and lint is not None \
                and lint.status == OK and before.span is not None:
            counterpart = counterpart_of(before.content, before.span, after,
                                         (path, change.new.path, c1.sha))
        touch = None if change.binary else line_touch(
            change.hunks, before.item.diff_line,
            git_span(before.content, before.span) if before.span is not None else None)
        status, tool = _resolver(c1)
        evidence = _Evidence(any(bulk), change.new is None,
                             lint is not None and lint.status != OK, before.span is None,
                             counterpart.similarity,
                             touch.exact if touch is not None else None, path,
                             before.item.line, c1.sha)
        author = self._commits[introduction.commit_sha]
        return Disappearance(
            bulk_a1=bulk[0], bulk_a2=bulk[1], bulk_a3=bulk[2], file_removed=change.new is None,
            parse_failure_reason=failure, counterpart=counterpart, touch=touch,
            resolver_trace_status=status, resolver_tool=tool,
            same_name=_same(author.author_name_key, c1.author_name_key),
            same_email=_same(author.author_email_key, c1.author_email_key),
            modes=tuple((tau, _mode(tau, evidence)) for tau in TAUS))

    def _before(self, introduction: Introduction, tracking: Tracking, c1_sha: str) -> _Before:
        """Return V0 with the item's occurrence and, unless module-level, its scope span."""
        path, blob = str(tracking.path_before_c1), str(tracking.blob_before_c1)
        content = self._source.read_blob(blob)
        lint = self._source.lint(content, path, blob)
        item = next((occurrence for occurrence in occurrences(lint.messages, content)
                     if occurrence.key == introduction.key
                     and occurrence.line == tracking.line_before_c1), None)
        if lint.status != OK or item is None:
            raise RuntimeError(f"the item is not in {path} before {c1_sha}")
        if introduction.scope == MODULE_LEVEL:
            return _Before(content, item, None)
        try:
            span = locate(scope_spans(content), introduction.scope, item.line)
        except (SyntaxError, ValueError):
            raise version_unreadable(path, c1_sha, before=True) from None
        if span is None:
            raise scope_unlocatable(path, item.line, c1_sha)
        return _Before(content, item, span)

    def _after(self, change: FileChange) -> tuple[bytes | None, LintResult | None]:
        """Return V1 and its lint result, or (None, None) when c1 removes the file."""
        if change.new is None:
            return None, None
        content = self._source.read_blob(change.new.blob)
        return content, self._source.lint(content, change.new.path, change.new.blob)


def unit_summaries(disappearances: Sequence[Disappearance]) -> tuple[UnitSummary, ...]:
    """Return a unit's summary at each tau: disappearances, local fixes and their share."""
    summaries = []
    for tau in TAUS:
        fixes = sum(1 for found in disappearances if found.mode_at(tau) == LOCAL_FIX)
        count = len(disappearances)
        summaries.append(UnitSummary(tau, count, fixes, fixes / count if count else None))
    return tuple(summaries)


def _mode(tau: float, evidence: _Evidence) -> str:
    """Return the mode at ``tau`` in FR-56's order of precedence."""
    if evidence.bulk:
        return BULK
    if evidence.removed:
        return REMOVED
    if evidence.lint_failed:
        return ARTIFACT
    if evidence.module_level:
        raise module_level_undecided(evidence.path, evidence.line, evidence.c1_sha)
    if evidence.similarity is None:
        return DELETED
    if evidence.similarity < tau:
        return REWRITTEN
    if evidence.exact_touch is None:
        raise RuntimeError("a linting version at c1 has hunks")
    return LOCAL_FIX if evidence.exact_touch else ARTIFACT


def counterpart_of(before: bytes, span: ScopeSpan, after: bytes,
                   where: tuple[str, str, str]) -> Counterpart:
    """Return the counterpart of the item's scope in V1 (D-1) and its similarity (D-2).

    ``where`` is (path before c1, path at c1, c1's SHA), for a fail-closed message.
    """
    path_before, path_after, c1_sha = where
    try:
        own = scope_tokens(python_tokens(before), span)
    except SourceUnreadable:
        raise version_unreadable(path_before, c1_sha, before=True) from None
    try:
        tokens = python_tokens(after)
        spans = scope_spans(after)
    except (SourceUnreadable, SyntaxError, ValueError):
        raise version_unreadable(path_after, c1_sha, before=False) from None
    same = [candidate for candidate in spans if candidate.name == span.name]
    if same:
        found, ratio = _most_similar(own, same, tokens)
        return Counterpart(SAME_NAME, found.name, ratio)
    kind = [candidate for candidate in spans if candidate.kind == span.kind]
    if kind:
        found, ratio = _most_similar(own, kind, tokens)
        if ratio >= COUNTERPART_MIN_SIMILARITY:
            return Counterpart(MOST_SIMILAR, found.name, ratio)
    return Counterpart()


def _most_similar(own: Sequence[tuple[int, str]], candidates: Sequence[ScopeSpan],
                  tokens: Sequence) -> tuple[ScopeSpan, float]:
    """Return the most similar candidate and its similarity; ties go to the first."""
    best, best_ratio = candidates[0], -1.0
    for candidate in candidates:
        ratio = similarity(own, scope_tokens(tokens, candidate))
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best, best_ratio


def _change_of(c1: RecordedCommit, path: str) -> FileChange:
    """Return c1's one change of the item's file."""
    found = [entry.change for entry in c1.changes
             if entry.change.old is not None and entry.change.old.path == path]
    if len(found) != 1:
        if found:
            raise change_inconsistent(c1.sha, path)
        raise RuntimeError(f"c1 {c1.sha} does not change {path}")
    return found[0]


def _resolver(c1: RecordedCommit) -> tuple[str, str | None]:
    """Return c1's trace status and tool; an unknown status fails closed."""
    status = c1.provenance.status
    if not isinstance(status, TraceStatus):
        raise provenance_unknown(c1.sha)
    return status.value, c1.provenance.tool


def _same(left: str | None, right: str | None) -> bool | None:
    """Compare two identity keys; null if either is null."""
    return None if left is None or right is None else left == right
