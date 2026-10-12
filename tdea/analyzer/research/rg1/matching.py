"""Item keys and matching (FR-53, D-12).

A debt item is identified by its key: rule symbol + normalised flagged-line text
(trimmed, inner whitespace collapsed) + enclosing-scope name (FR-53). Between a file's
parent version and its version at commit c, the occurrences of each key are matched
first through the diff's unchanged-line mapping, then in line order; occurrences left
over after c are new, those left over before c have disappeared (D-12). That rule is
applied exactly; there are no count thresholds and no similarity measure.

The flagged line is the source line Pylint reports for the message, counted as Python
counts lines (a line ends at CR LF, LF or CR), which is how Pylint numbers them. The
hunks (FR-50) count lines as Git does, ending them at LF only. Each occurrence therefore
carries both numbers: its line (Pylint's, which is recorded) and its diff line (Git's
line holding the start of that Python line, used for the mapping and the added side).
The two differ only in a file with a CR that is not followed by LF. The unchanged-line
mapping keeps an old line outside every removed range in its place, shifted by the lines
added and removed above it.

Readings where the frozen texts are silent (Task 7 report): the occurrence index is the
0-based position of an occurrence among those of its key in one version, ordered by line
and then by Pylint's output order; the key's stored text form is the JSON array
[symbol, normalised text, scope]; a message whose line lies outside the file (Pylint
reports a few module messages so) has empty flagged-line text.
"""

from __future__ import annotations

import io
import json
import re
import tokenize
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from tdea.analyzer.research.profile import LintMessage
from tdea.git_handler.file_changes import Hunk

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class ItemKey:
    """Rule symbol + normalised flagged-line text + enclosing-scope name (FR-53)."""

    symbol: str
    text: str
    scope: str

    def serialised(self) -> str:
        """Return the key's stored text form: the JSON array [symbol, text, scope]."""
        return json.dumps([self.symbol, self.text, self.scope], ensure_ascii=False,
                          separators=(",", ":"))


@dataclass(frozen=True)
class Occurrence:
    """One message of one version, with its key and occurrence index."""

    key: ItemKey
    message: LintMessage
    index: int
    ordinal: int
    diff_line: int  # Git's line number for the flagged line, used against the hunks

    @property
    def line(self) -> int:
        """Return the flagged line, as Pylint numbers it."""
        return self.message.line


@dataclass(frozen=True)
class MatchResult:
    """How the occurrences of a parent version and a version at c correspond."""

    matched: tuple[tuple[Occurrence, Occurrence], ...]
    new: tuple[Occurrence, ...]
    disappeared: tuple[Occurrence, ...]


def normalise(text: str) -> str:
    """Return ``text`` trimmed, with each run of inner whitespace collapsed to one space."""
    return " ".join(text.split())


def source_lines(content: bytes) -> tuple[str, ...]:
    """Return the lines of Python source as Python numbers them (PEP 263 decoding)."""
    return _split(content)[0]


def git_line_numbers(content: bytes) -> tuple[int, ...]:
    """Return, for each Python line of ``content`` in order, Git's number for its line."""
    return _split(content)[1]


def _split(content: bytes) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Return the Python lines of ``content`` and, for each, Git's number for its line."""
    encoding, _consumed = tokenize.detect_encoding(io.BytesIO(content).readline)
    text = content.decode(encoding)
    lines: list[str] = []
    diff_lines: list[int] = []
    line_feeds = 0
    start = 0
    for found in _LINE_BREAK.finditer(text):
        lines.append(text[start:found.start()])
        diff_lines.append(line_feeds + 1)
        line_feeds += found.group().count("\n")
        start = found.end()
    if start < len(text):  # a last line without a line break
        lines.append(text[start:])
        diff_lines.append(line_feeds + 1)
    return tuple(lines), tuple(diff_lines)


def item_key(message: LintMessage, lines: Sequence[str]) -> ItemKey:
    """Return the key of ``message`` in a version whose lines are ``lines``."""
    text = lines[message.line - 1] if 1 <= message.line <= len(lines) else ""
    return ItemKey(message.symbol, normalise(text), message.scope)


def occurrences(messages: Sequence[LintMessage], content: bytes) -> tuple[Occurrence, ...]:
    """Return one occurrence per message, ordered by line and then by output order."""
    lines, diff_lines = _split(content) if messages else ((), ())
    ordered = sorted(enumerate(messages), key=lambda pair: (pair[1].line, pair[0]))
    seen: dict[ItemKey, int] = defaultdict(int)
    result = []
    for ordinal, message in ordered:
        key = item_key(message, lines)
        diff_line = (diff_lines[message.line - 1] if 1 <= message.line <= len(diff_lines)
                     else message.line)
        result.append(Occurrence(key, message, seen[key], ordinal, diff_line))
        seen[key] += 1
    return tuple(result)


def unchanged_line_map(hunks: Sequence[Hunk]) -> Callable[[int], int | None]:
    """Return the diff's unchanged-line mapping: old line -> new line, or None if removed.

    A hunk with old length 0 inserts its lines after old line ``old_start``; any other
    hunk removes old lines ``old_start`` to ``old_start + old_length - 1``.
    """
    ordered = sorted(hunks, key=lambda hunk: (hunk.old_start, hunk.new_start))

    def mapping(line: int) -> int | None:
        """Map one old line."""
        offset = 0
        for hunk in ordered:
            if hunk.old_length:
                if hunk.old_start <= line < hunk.old_start + hunk.old_length:
                    return None
                if line >= hunk.old_start + hunk.old_length:
                    offset += hunk.new_length - hunk.old_length
            elif line > hunk.old_start:
                offset += hunk.new_length
        return line + offset

    return mapping


def on_added_side(line: int, hunks: Sequence[Hunk]) -> bool:
    """Return True if new line ``line`` lies in the added range of one of ``hunks``."""
    return any(hunk.new_length and hunk.new_start <= line < hunk.new_start + hunk.new_length
               for hunk in hunks)


class ItemMatcher:
    """Matches item occurrences across a parent version and a version at c (D-12)."""

    def match(self, before: Sequence[Occurrence], after: Sequence[Occurrence],
              hunks: Sequence[Hunk]) -> MatchResult:
        """Pair occurrences of each key: unchanged-line mapping first, then line order."""
        matched, left_before, left_after = _match_unchanged_lines(before, after, hunks)
        new, disappeared = _match_in_line_order(left_before, left_after, matched)
        return MatchResult(tuple(sorted(matched, key=lambda pair: _position(pair[1]))),
                           tuple(sorted(new, key=_position)),
                           tuple(sorted(disappeared, key=_position)))


def _match_unchanged_lines(before: Sequence[Occurrence], after: Sequence[Occurrence],
                           hunks: Sequence[Hunk]) -> tuple[list, list, list]:
    """First phase: pair each old occurrence with an equal key on its mapped line.

    Return the pairs and the occurrences of each version left unpaired, in order.
    """
    mapping = unchanged_line_map(hunks)
    waiting: dict[tuple[ItemKey, int], list[Occurrence]] = defaultdict(list)
    for occurrence in _in_order(after):
        waiting[(occurrence.key, occurrence.diff_line)].append(occurrence)
    matched: list[tuple[Occurrence, Occurrence]] = []
    left_before: list[Occurrence] = []
    for old in _in_order(before):
        target = mapping(old.diff_line)
        candidates = waiting.get((old.key, target)) if target is not None else None
        if candidates:
            matched.append((old, candidates.pop(0)))
        else:
            left_before.append(old)
    unpaired = {id(occurrence) for group in waiting.values() for occurrence in group}
    left_after = [occurrence for occurrence in _in_order(after) if id(occurrence) in unpaired]
    return matched, left_before, left_after


def _match_in_line_order(left_before: list[Occurrence], left_after: list[Occurrence],
                         matched: list[tuple[Occurrence, Occurrence]]
                         ) -> tuple[list[Occurrence], list[Occurrence]]:
    """Second phase: pair the rest of each key in line order; return (new, disappeared)."""
    by_key_before: dict[ItemKey, list[Occurrence]] = defaultdict(list)
    by_key_after: dict[ItemKey, list[Occurrence]] = defaultdict(list)
    for occurrence in left_before:
        by_key_before[occurrence.key].append(occurrence)
    for occurrence in left_after:
        by_key_after[occurrence.key].append(occurrence)
    new: list[Occurrence] = []
    disappeared: list[Occurrence] = []
    for key in sorted(set(by_key_before) | set(by_key_after), key=_key_order):
        olds, news = by_key_before[key], by_key_after[key]
        matched.extend(zip(olds, news))
        disappeared.extend(olds[len(news):])
        new.extend(news[len(olds):])
    return new, disappeared


def _position(occurrence: Occurrence) -> tuple[int, int]:
    """Return an occurrence's place in its version: line, then output order."""
    return occurrence.line, occurrence.ordinal


def _in_order(items: Sequence[Occurrence]) -> list[Occurrence]:
    """Return occurrences sorted by their place in their version."""
    return sorted(items, key=_position)


def _key_order(key: ItemKey) -> tuple[str, str, str]:
    """Return a fixed order for keys, so results never depend on hashing."""
    return key.symbol, key.text, key.scope
