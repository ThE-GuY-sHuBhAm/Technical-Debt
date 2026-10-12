"""RE-07 (FR-53, D-12): item identity and matching on FX-PAIRS, plus the mapping rules.

Each FX-PAIRS pair is a before and an after commit on one file. Both versions are read
from a clone, linted with the research profile, turned into occurrences and matched with
the commit's own hunks, exactly as FR-53 states: unchanged-line mapping first, then line
order. FX-PAIRS is unchanged. The flagged lines are f-strings without interpolation
(W1309).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import make_fx_pairs
import pytest

from tdea.analyzer.research.profile import LintMessage, ResearchProfile
from tdea.analyzer.research.rg1.matching import (ItemKey, ItemMatcher, MatchResult,
                                                 occurrences, on_added_side, source_lines,
                                                 unchanged_line_map)
from tdea.git_handler.file_changes import Hunk
from tdea.git_handler.history_reader import HistoryReader
from tdea.git_handler.local_repository import LocalRepository

W1309 = "f-string-without-interpolation"


@dataclass(frozen=True)
class Matched:
    """One pair's match and the keys of its two versions."""

    result: MatchResult
    before_keys: tuple[ItemKey, ...]
    after_keys: tuple[ItemKey, ...]


@pytest.fixture(name="pairs", scope="module")
def _pairs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Matched]]:
    """Build and clone FX-PAIRS, then match every pair with real lint results and hunks."""
    root = tmp_path_factory.mktemp("fx-pairs")
    built = make_fx_pairs.build(root / "repo")
    (root / "clones").mkdir()
    profile = ResearchProfile()
    with LocalRepository(root / "clones") as local:
        local.clone(root / "repo")
        reader = HistoryReader(local)
        matched = {}
        for name, pair in built.items():
            changes = reader.file_changes(pair.after_sha)
            assert len(changes) == 1
            change = changes[0]
            versions = []
            for sha in (pair.before_sha, pair.after_sha):
                content = reader.read_blob(sha, pair.path)
                lint = profile.lint(content, Path(pair.path).name)
                assert lint.status == "ok"
                versions.append(occurrences(lint.messages, content))
            result = ItemMatcher().match(versions[0], versions[1], change.hunks)
            matched[name] = Matched(result, tuple(o.key for o in versions[0]),
                                    tuple(o.key for o in versions[1]))
        yield matched


def lines(result: MatchResult) -> tuple[list, list, list]:
    """Return the matched line pairs, new lines and disappeared lines."""
    return ([(old.line, new.line) for old, new in result.matched],
            [occurrence.line for occurrence in result.new],
            [occurrence.line for occurrence in result.disappeared])


def test_re07_a_a_line_moved_5_lines_keeps_its_item(pairs: dict[str, Matched]) -> None:
    """(a) The flagged line moves from line 3 to line 8: same item, matched by the mapping."""
    assert lines(pairs["a_moved"].result) == ([(3, 8)], [], [])
    assert pairs["a_moved"].before_keys == pairs["a_moved"].after_keys == (
        ItemKey(W1309, 'print(f"start")', "run"),)


def test_re07_b_one_of_two_identical_lines_removed(pairs: dict[str, Matched]) -> None:
    """(b) Two identical lines, one removed: one item persists and one disappears."""
    result = pairs["b_duplicate"].result
    assert lines(result) == ([(3, 3)], [], [4])
    assert [o.index for o in result.disappeared] == [1]  # the second occurrence of its key


def test_re07_c_one_symbol_in_two_functions_gives_two_items(pairs: dict[str, Matched]) -> None:
    """(c) The same flagged text in two functions is two keys, so two items."""
    keys = pairs["c_two_scopes"].after_keys
    assert keys == (ItemKey(W1309, 'print(f"same")', "first"),
                    ItemKey(W1309, 'print(f"same")', "second"))
    assert lines(pairs["c_two_scopes"].result) == ([(3, 3)], [8], [])


def test_re07_d_a_whitespace_only_change_keeps_its_item(pairs: dict[str, Matched]) -> None:
    """(d) The line is in the hunk, but its normalised text is unchanged: matched in line order."""
    assert lines(pairs["d_whitespace"].result) == ([(3, 3)], [], [])
    assert pairs["d_whitespace"].before_keys == pairs["d_whitespace"].after_keys == (
        ItemKey(W1309, 'print(f"done", end="")', "label"),)


def test_re08_pair_new_occurrences_are_the_added_and_modified_lines(
    pairs: dict[str, Matched]
) -> None:
    """In the RE-08 pair, only the modified (4) and added (5) lines are new; 3 persists."""
    assert lines(pairs["e_added_modified_untouched"].result) == ([(3, 3)], [4, 5], [4])


# --- the mapping and its parts, on hand-made inputs ------------------------------------

def message(line: int, scope: str = "f", symbol: str = W1309) -> LintMessage:
    """Return a lint message on ``line`` (test data)."""
    return LintMessage(symbol, "W1309", "warning", "Using an f-string", line, scope)


@pytest.mark.parametrize("hunks, expected", [
    ((), {1: 1, 5: 5}),
    ((Hunk(0, 0, 1, 2),), {1: 3, 4: 6}),                     # 2 lines inserted at the top
    ((Hunk(2, 0, 3, 5),), {2: 2, 3: 8}),                     # 5 lines after old line 2
    ((Hunk(4, 1, 3, 0),), {3: 3, 4: None, 5: 4}),            # old line 4 removed
    ((Hunk(3, 1, 3, 1),), {2: 2, 3: None, 4: 4}),            # line 3 replaced
    ((Hunk(1, 2, 1, 0), Hunk(5, 0, 4, 3)), {1: None, 3: 1, 5: 3, 6: 7}),
])
def test_unchanged_line_mapping(hunks: tuple[Hunk, ...], expected: dict[int, int | None]) -> None:
    """Old lines outside removed ranges keep their place, shifted by the lines above."""
    mapping = unchanged_line_map(hunks)
    assert {line: mapping(line) for line in expected} == expected


def test_added_side_and_line_splitting() -> None:
    """Added ranges are new-side; lines split as Python splits them, PEP 263 honoured."""
    assert on_added_side(4, (Hunk(4, 1, 4, 2),)) and on_added_side(5, (Hunk(4, 1, 4, 2),))
    assert not on_added_side(6, (Hunk(4, 1, 4, 2),)) and not on_added_side(3, (Hunk(3, 1, 3, 0),))
    assert source_lines(b"a\r\nb\rc\n") == ("a", "b", "c")
    assert source_lines(b"# -*- coding: latin-1 -*-\nx = '\xe9'\n")[1] == "x = 'é'"


def test_keys_normalise_whitespace_and_name_an_out_of_range_line_empty() -> None:
    """Flagged text is trimmed and collapsed; a line past the end has empty text."""
    found = occurrences([message(1), message(9)], b"\tprint( f'a'  ,  1 )  \n")
    assert [o.key.text for o in found] == ["print( f'a' , 1 )", ""]
    assert found[0].key.serialised() == f'["{W1309}","print( f\'a\' , 1 )","f"]'


def test_identical_messages_on_one_line_pair_in_output_order() -> None:
    """Two equal keys on one unchanged line pair one to one, in Pylint's output order."""
    content = b"x = f'a' + f'a'\n"
    before = occurrences([message(1), message(1)], content)
    after = occurrences([message(1), message(1)], content)
    result = ItemMatcher().match(before, after, ())
    assert [(old.ordinal, new.ordinal) for old, new in result.matched] == [(0, 0), (1, 1)]
    assert [o.index for o in after] == [0, 1]


def test_line_order_pairs_leftovers_from_the_top() -> None:
    """Occurrences not paired by the mapping pair in line order; the rest are new or gone."""
    before = occurrences([message(2), message(3)], b"a\nprint(f'x')\nprint(f'x')\n")
    after = occurrences([message(2), message(5), message(7)],
                        b"a\nprint(f'x')\nb\nc\nprint(f'x')\nd\nprint(f'x')\n")
    result = ItemMatcher().match(before, after, (Hunk(2, 2, 2, 6),))
    assert lines(result) == ([(2, 2), (3, 5)], [7], [])


def test_an_added_file_has_only_new_and_a_deleted_file_only_disappeared() -> None:
    """A file added by c has an empty parent version (FR-53); a deleted one an empty version."""
    found = occurrences([message(1)], b"print(f'x')\n")
    assert lines(ItemMatcher().match((), found, (Hunk(0, 0, 1, 1),))) == ([], [1], [])
    assert lines(ItemMatcher().match(found, (), (Hunk(1, 1, 0, 0),))) == ([], [], [1])


def test_results_do_not_depend_on_input_order() -> None:
    """Shuffled inputs give the same result; nothing depends on hashing or arrival order."""
    content = b"def f():\n    print(f'x')\n    print(f'y')\n"
    before = occurrences([message(2), message(3)], content)
    after = occurrences([message(3), message(2)], content)
    forward = ItemMatcher().match(before, after, ())
    backward = ItemMatcher().match(tuple(reversed(before)), tuple(reversed(after)), ())
    assert lines(forward) == lines(backward) == ([(2, 2), (3, 3)], [], [])


def test_the_unchanged_line_mapping_runs_before_line_order() -> None:
    """A copy inserted above two identical lines is the new one, as D-12 orders the phases.

    Line order alone would pair the old lines with the new copy and the first shifted
    line, and call the last line new; the mapping pairs each old line with its shifted
    self, so the inserted line 1 is the new occurrence.
    """
    before = occurrences([message(2), message(4)], b"a\nprint(f'x')\nb\nprint(f'x')\n")
    after = occurrences([message(1), message(3), message(5)],
                        b"print(f'x')\na\nprint(f'x')\nb\nprint(f'x')\n")
    result = ItemMatcher().match(before, after, (Hunk(0, 0, 1, 1),))
    assert lines(result) == ([(2, 3), (4, 5)], [1], [])


def test_a_lone_cr_file_is_mapped_with_git_lines() -> None:
    """Python splits at a lone CR and Git does not; the mapping uses Git's line numbers.

    Before: Git line 1 holds Python lines 1 and 2, Git line 2 is Python line 3. A copy is
    inserted as Git line 2. Each old occurrence pairs with its own shifted self, and the
    inserted copy (Python line 3, Git line 2) is the new one.
    """
    before = occurrences([message(2), message(3)], b"a = 1\rprint(f'x')\nprint(f'x')\n")
    after = occurrences([message(2), message(3), message(4)],
                        b"a = 1\rprint(f'x')\nprint(f'x')\nprint(f'x')\n")
    assert [o.diff_line for o in after] == [1, 2, 3]
    result = ItemMatcher().match(before, after, (Hunk(1, 0, 2, 1),))
    assert lines(result) == ([(2, 2), (3, 4)], [3], [])
    