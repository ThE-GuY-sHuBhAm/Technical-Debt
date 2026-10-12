"""FR-56 signals: similarity (D-2), formatting-only lines (D-3) and line touch (D-5).

Tokens. A file version is tokenised whole, as Python reads it: decoded by PEP 263 rules
and read with universal newlines, so CR LF, LF and CR all end a line and token lines are
Python's (the lines ast and Pylint use). A version that cannot be decoded or tokenised
raises SourceUnreadable. Tokens are compared as (type, string) pairs.

D-2 similarity (Task 7 Day 7 reading R4, S1, approved provisionally): difflib
SequenceMatcher ratio with autojunk off over the tokens that start on a scope's lines,
from its def or class line to its last line (nested scopes included, decorators
excluded), ignoring exactly D-2's NL, NEWLINE, INDENT, DEDENT and COMMENT. The ratio is
kept unrounded.

D-3 formatting-only lines (reading R2, I3, approved provisionally): a hunk's removed side
is the sequence of tokens of the old version that overlap its removed lines, and its
added side the tokens of the new version that overlap its added lines, ignoring exactly
D-3's NL, NEWLINE, INDENT and DEDENT (COMMENT is kept). When the two sequences are equal,
every changed line of the hunk (removed and added) is formatting-only. Hunks count lines
as Git does, so token lines are mapped to Git lines first (they differ only in a file with
a CR not followed by LF). A file whose version cannot be read, or a binary change, has no
formatting-only lines.

D-5 line touch, on the hunks of c1 against the version before c1, in Git's line numbers:
exact = the warning's line lies in a removed range (D-5, decides mode e); near = some
removed line lies within 3 lines of it; scope = a removed line inside the item's scope,
or a pure insertion between two lines of the scope (for a module-level item, any hunk in
the file). Near and scope ignore nothing else and decide no mode (reading P2, approved
provisionally as signals only).
"""

from __future__ import annotations

import io
import tokenize
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from tdea.analyzer.research.rg1.defaults import (FORMATTING_IGNORED, NEAR_LINES,
                                                 SIMILARITY_IGNORED)
from tdea.analyzer.research.rg1.matching import git_line_numbers
from tdea.analyzer.research.rg1.scopes import ScopeSpan
from tdea.git_handler.file_changes import FileChange, Hunk

Token = tuple[int, str]


class SourceUnreadable(Exception):
    """A file version cannot be decoded or tokenised."""


@dataclass(frozen=True)
class SourceToken:
    """One token of a file version, with its first and last Python line."""

    kind: int
    text: str
    first: int
    last: int


@dataclass(frozen=True)
class LineTouch:
    """D-5's three line-touch variants of c1 on one item."""

    exact: bool
    near: bool
    scope: bool


def python_tokens(content: bytes) -> tuple[SourceToken, ...]:
    """Return every token of one file version, or raise SourceUnreadable."""
    try:
        encoding, _consumed = tokenize.detect_encoding(io.BytesIO(content).readline)
        text = content.decode(encoding)
        readline = io.StringIO(text, newline=None).readline
        return tuple(SourceToken(token.type, token.string, token.start[0], token.end[0])
                     for token in tokenize.generate_tokens(readline))
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        raise SourceUnreadable(type(exc).__name__) from None


def scope_tokens(tokens: Sequence[SourceToken], span: ScopeSpan) -> tuple[Token, ...]:
    """Return the D-2 token sequence of one scope: tokens starting on its lines."""
    return tuple((token.kind, token.text) for token in tokens
                 if span.first <= token.first <= span.last
                 and token.kind not in SIMILARITY_IGNORED)


def similarity(before: Sequence[Token], after: Sequence[Token]) -> float:
    """Return D-2's similarity of two token sequences (difflib ratio, autojunk off)."""
    return SequenceMatcher(None, before, after, autojunk=False).ratio()


def formatting_only_lines(change: FileChange, before: bytes | None,
                          after: bytes | None) -> tuple[int, int]:
    """Return (formatting-only changed lines, changed lines) of one changed file (D-3)."""
    if change.binary:
        return 0, 0
    changed = sum(hunk.old_length + hunk.new_length for hunk in change.hunks)
    if not changed:
        return 0, 0
    try:
        old = _git_tokens(before) if before is not None else ()
        new = _git_tokens(after) if after is not None else ()
    except SourceUnreadable:
        return 0, changed
    formatting = sum(hunk.old_length + hunk.new_length for hunk in change.hunks
                     if _overlapping(old, hunk.old_start, hunk.old_length)
                     == _overlapping(new, hunk.new_start, hunk.new_length))
    return formatting, changed


def line_touch(hunks: Sequence[Hunk], warning_line: int,
               scope_lines: tuple[int, int] | None) -> LineTouch:
    """Return D-5's touches of ``hunks`` on a warning's Git line and its scope's Git lines.

    ``scope_lines`` is (first, last) in Git's numbering, or None for a module-level item.
    """
    removed = [(hunk.old_start, hunk.old_start + hunk.old_length - 1)
               for hunk in hunks if hunk.old_length]
    exact = any(first <= warning_line <= last for first, last in removed)
    near = any(first - NEAR_LINES <= warning_line <= last + NEAR_LINES
               for first, last in removed)
    if scope_lines is None:
        scope = bool(hunks)
    else:
        top, bottom = scope_lines
        scope = (any(first <= bottom and last >= top for first, last in removed)
                 or any(top <= hunk.old_start < bottom for hunk in hunks
                        if not hunk.old_length))
    return LineTouch(exact, near, scope)


def git_span(content: bytes, span: ScopeSpan) -> tuple[int, int]:
    """Return a scope's first and last line in Git's numbering."""
    lines = git_line_numbers(content)
    return _git_line(lines, span.first), _git_line(lines, span.last)


def _git_tokens(content: bytes) -> tuple[tuple[int, str, int, int], ...]:
    """Return (type, string, first Git line, last Git line) of the D-3 tokens of a version."""
    tokens = python_tokens(content)  # raises SourceUnreadable before any other decoding
    lines = git_line_numbers(content)
    return tuple((token.kind, token.text, _git_line(lines, token.first),
                  _git_line(lines, token.last))
                 for token in tokens if token.kind not in FORMATTING_IGNORED)


def _git_line(lines: Sequence[int], python_line: int) -> int:
    """Return Git's line for a Python line; a line past the end maps past Git's last line."""
    if 1 <= python_line <= len(lines):
        return lines[python_line - 1]
    return (lines[-1] if lines else 0) + python_line - len(lines)


def _overlapping(tokens: Sequence[tuple[int, str, int, int]], start: int,
                 length: int) -> tuple[Token, ...]:
    """Return the tokens that overlap Git lines start to start + length - 1."""
    if not length:
        return ()
    end = start + length - 1
    return tuple((kind, text) for kind, text, first, last in tokens
                 if first <= end and last >= start)
