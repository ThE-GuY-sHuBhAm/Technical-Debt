"""FR-56 signals on hand-made inputs: scopes, similarity, formatting-only lines, touch.

Readings: scopes D-1 (R3), similarity D-2 (R4), formatting D-3 and D-13 (R2, R1), touch
D-5 (P2).

These are mechanics checks of the Task 7 Day 7 readings, approved provisionally; they are
not RE-11 evidence and validate nothing about the research methodology. Each test that
tells two readings apart says which reading it rules out. Hunks are written by hand in
Git's numbering, as FR-50's diff (--unified=0) gives them.
"""

from __future__ import annotations

from collections.abc import Sequence
from difflib import SequenceMatcher
import pytest

from tdea.analyzer.research.profile import ResearchProfile
from tdea.analyzer.research.rg1.bulk import formatting_sweep
from tdea.analyzer.research.rg1.disappearance import (MOST_SIMILAR, SAME_NAME, Counterpart,
                                                      counterpart_of)
from tdea.analyzer.research.rg1.scopes import (CLASS, FUNCTION, ScopeResolver, ScopeSpan,
                                               locate, scope_spans)
from tdea.analyzer.research.rg1.signals import (LineTouch, SourceUnreadable,
                                                formatting_only_lines, git_span, line_touch,
                                                python_tokens, scope_tokens, similarity)
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk

PROBE = b'''"""Probe."""
TOP = lambda: f"top"
PAIR = (lambda: f"left", lambda: f"right")


def outer(arg=lambda: f"default"):
    """Doc."""
    nested = lambda: (lambda: f"inner")
    items = [lambda: f"comp" for _ in range(2)]
    return nested, items, arg


class Box:
    """Doc."""

    key = lambda self: f"classattr"

    @decorate(lambda: f"deco")
    def method(self):
        """Doc."""
        return 1

    @property
    def total(self):
        """Doc."""
        return f"getter"

    @total.setter
    def total(self, value):
        """Doc."""
        self.value = f"setter"


async def fetch():
    """Doc."""
    return f"async"
'''


def change(hunks: Sequence[Hunk], old: str | None = "pkg/m.py", new: str | None = "pkg/m.py",
           binary: bool = False, kind: ChangeType = ChangeType.MODIFIED) -> FileChange:
    """Return a hand-made file change with blobs named after the paths."""
    return FileChange(kind, FileSide(old, f"old-{old}") if old else None,
                      FileSide(new, f"new-{new}") if new else None, None, binary,
                      tuple(hunks))


def span_of(source: bytes, name: str, index: int = 0) -> ScopeSpan:
    """Return the ``index``-th span named ``name``."""
    return [span for span in scope_spans(source) if span.name == name][index]


def ratio(before: bytes, after: bytes, name: str) -> float:
    """Return D-2's similarity of the first scope ``name`` in two versions."""
    return similarity(scope_tokens(python_tokens(before), span_of(before, name)),
                      scope_tokens(python_tokens(after), span_of(after, name)))


# --- Scopes (D-1, reading R3) -------------------------------------------------------------

def test_scope_spans_name_every_scope_as_pylint_does() -> None:
    """Every Pylint scope name in the probe is located, except two lambdas sharing a line."""
    spans = scope_spans(PROBE)
    lint = ResearchProfile().lint(PROBE, "probe.py")
    unlocated = sorted({(message.line, message.scope) for message in lint.messages
                        if message.scope and locate(spans, message.scope, message.line) is None})
    assert unlocated == [(3, "<lambda>")]
    scopes = {message.scope for message in lint.messages}
    assert {"<lambda>", "outer.<lambda>", "outer.<lambda>.<lambda>", "Box.<lambda>",
            "Box.total", "fetch"} <= scopes


def test_kinds_lines_and_order_of_spans() -> None:
    """Classes and functions (def, async def, lambda) as two kinds; decorators outside."""
    spans = scope_spans(PROBE)
    assert spans[0] == ScopeSpan("<lambda>", FUNCTION, 2, 2)
    assert span_of(PROBE, "Box") == ScopeSpan("Box", CLASS, 13, 31)
    assert span_of(PROBE, "Box.method") == ScopeSpan("Box.method", FUNCTION, 19, 21)
    assert span_of(PROBE, "fetch").kind == FUNCTION
    assert [span.first for span in spans] == sorted(span.first for span in spans)
    assert [span.name for span in spans].count("Box.total") == 2


def test_locate_needs_exactly_one_span() -> None:
    """None for no holding span, None for several, the span for exactly one."""
    spans = scope_spans(PROBE)
    assert locate(spans, "Box.total", 26) == ScopeSpan("Box.total", FUNCTION, 24, 26)
    assert locate(spans, "Box.total", 31) == ScopeSpan("Box.total", FUNCTION, 29, 31)
    assert locate(spans, "Box.total", 2) is None
    assert locate(spans, "<lambda>", 3) is None


def test_scope_at_is_unchanged_and_ignores_lambdas() -> None:
    """ScopeResolver (Task 6) still names lines without lambdas."""
    resolver = ScopeResolver(PROBE)
    assert resolver.scope_at(8) == "outer" and resolver.scope_at(2) == ""
    assert resolver.scope_at(18) == "Box"


GETTER_SETTER = b'''class Cart:
    """Doc."""

    @property
    def total(self):
        """Return the total."""
        return self.amount

    @total.setter
    def total(self, value):
        """Set the total."""
        self.amount = f"{value}" if value else f"none"
'''


def test_repeated_names_take_the_most_similar_not_the_same_position() -> None:
    """R3 B1: the getter goes; the setter (first same-named scope now) is still found.

    A positional reading (second same-named scope to second) finds nothing and would
    report "function deleted".
    """
    setter = span_of(GETTER_SETTER, "Cart.total", 1)
    after = GETTER_SETTER.replace(
        b'    @property\n    def total(self):\n        """Return the total."""\n'
        b'        return self.amount\n\n', b"")
    found = counterpart_of(GETTER_SETTER, setter, after, ("m.py", "m.py", "c1"))
    assert found.method == SAME_NAME and found.scope == "Cart.total"
    assert found.similarity == 1.0


def test_swapped_repeated_names_still_pair_by_similarity() -> None:
    """R3 B1: the setter now comes first; the getter's counterpart is still the getter."""
    getter = span_of(GETTER_SETTER, "Cart.total", 0)
    head, getter_text, setter_text = GETTER_SETTER.split(b"\n\n")
    swapped = b"\n\n".join((head, setter_text.rstrip(b"\n"), getter_text)) + b"\n"
    found = counterpart_of(GETTER_SETTER, getter, swapped, ("m.py", "m.py", "c1"))
    assert found.similarity == 1.0
    assert span_of(swapped, "Cart.total", 1).first > span_of(swapped, "Cart.total", 0).first


def test_lambdas_are_function_scopes_found_by_name_and_similarity() -> None:
    """R3 C1: of two same-named lambdas, the edited second one is the counterpart."""
    before = b'def outer():\n    first = lambda: 1\n    second = lambda: f"two" + "x"\n'
    after = b'def outer():\n    first = lambda: 1\n    second = lambda: "two" + "x"\n'
    second = span_of(before, "outer.<lambda>", 1)
    found = counterpart_of(before, second, after, ("m.py", "m.py", "c1"))
    own = scope_tokens(python_tokens(before), second)
    tokens = python_tokens(after)
    to_first = similarity(own, scope_tokens(tokens, span_of(after, "outer.<lambda>", 0)))
    to_second = similarity(own, scope_tokens(tokens, span_of(after, "outer.<lambda>", 1)))
    assert to_second > to_first
    assert found == Counterpart(SAME_NAME, "outer.<lambda>", to_second)


def test_a_renamed_def_made_async_is_still_the_same_kind() -> None:
    """R3 C1: def and async def are one kind, so the renamed scope is found by similarity.

    With four kinds (C2) the async function would not be a candidate and the item's
    function would count as deleted.
    """
    before = b'def load(path):\n    """Load."""\n    data = read(path)\n    return f"x", data\n'
    after = (b'async def fetch(path):\n    """Load."""\n    data = read(path)\n'
             b'    return "x", data\n')
    found = counterpart_of(before, span_of(before, "load"), after, ("m.py", "m.py", "c1"))
    assert found.method == MOST_SIMILAR and found.scope == "fetch"
    assert found.similarity >= 0.8


def test_no_counterpart_below_0_8_and_ties_go_to_the_first() -> None:
    """D-1: nothing at or above 0.8 means no counterpart; equal candidates pick the first."""
    before = b'def gone():\n    return f"x"\n\n\ndef other(a, b, c):\n    return a + b * c - 1\n'
    after = b'def other(a, b, c):\n    return a + b * c - 1\n'
    assert counterpart_of(before, span_of(before, "gone"), after,
                          ("m.py", "m.py", "c1")) == Counterpart()
    twins = b'def twin():\n    return 1\n\n\ndef twin():\n    return 1\n'
    found = counterpart_of(twins, span_of(twins, "twin"), twins, ("m.py", "m.py", "c1"))
    assert found.similarity == 1.0
    assert span_of(twins, "twin", 0).first == 1


def test_a_renamed_class_is_found_among_classes() -> None:
    """D-1: the most similar scope of the same kind; a function is no candidate for a class."""
    before = b'class Old:\n    """Doc."""\n    size = f"1"\n    name = "n"\n'
    after = (b'def helper():\n    """Doc."""\n    size = "1"\n    name = "n"\n\n\n'
             b'class New:\n    """Doc."""\n    size = "1"\n    name = "n"\n')
    found = counterpart_of(before, span_of(before, "Old"), after, ("m.py", "m.py", "c1"))
    assert found.method == MOST_SIMILAR and found.scope == "New"


def test_the_same_name_step_does_not_look_at_kind() -> None:
    """D-1 as written: "the scope with the same qualified name" comes first, of any kind."""
    before = b'class Old:\n    """Doc."""\n    size = f"1"\n'
    after = b'def Old():\n    """Doc."""\n    return "1"\n'
    found = counterpart_of(before, span_of(before, "Old"), after, ("m.py", "m.py", "c1"))
    assert found.method == SAME_NAME and found.scope == "Old"


# --- Similarity (D-2, reading R4, S1) ------------------------------------------------------

def test_a_decorator_change_leaves_similarity_one() -> None:
    """S1: decorators lie outside the scope's lines (S2 would count the change)."""
    before = b'@first\ndef f():\n    return 1\n'
    after = b'@second(option=True)\ndef f():\n    return 1\n'
    assert ratio(before, after, "f") == 1.0


def test_comments_and_layout_are_ignored_but_tokens_count() -> None:
    """D-2 ignores NL, NEWLINE, INDENT, DEDENT and COMMENT, and nothing else."""
    before = b'def f(a):\n    return a  # note\n'
    after = b'def f(a):\n    # another note\n\n    return (a)\n'
    assert ratio(before, b'def f(a):\n    # x\n\n\n    return a\n', "f") == 1.0
    assert ratio(before, after, "f") < 1.0  # the parentheses are tokens


def test_nested_scopes_count_towards_the_outer_scope() -> None:
    """S1: a change inside a nested function lowers the outer function's similarity."""
    before = b'def outer():\n    def inner():\n        return 1\n    return inner\n'
    after = b'def outer():\n    def inner():\n        return 2 + 3\n    return inner\n'
    assert ratio(before, after, "outer") < 1.0
    assert ratio(before, after, "outer.inner") < ratio(before, after, "outer")


def test_similarity_is_the_exact_unrounded_ratio() -> None:
    """2 x matches / total tokens, unrounded: 9 of 10 tokens kept gives exactly 0.9."""
    before = b'def local():\n    """Return a value."""\n    return f"local", 1\n'
    after = b'def local():\n    """Return a value."""\n    return "local", 1\n'
    assert ratio(before, after, "local") == 0.9
    assert similarity((), ()) == 1.0


def test_tokens_follow_python_lines_for_every_line_ending() -> None:
    """CR LF, LF and CR all end a line, as Python reads source."""
    tokens = python_tokens(b"x = 1\r\ny = 2\rz = 3\n")
    assert [(token.text, token.first) for token in tokens if token.kind == 1] == [
        ("x", 1), ("y", 2), ("z", 3)]
    with pytest.raises(SourceUnreadable):
        python_tokens(b'x = """open\n')
    with pytest.raises(SourceUnreadable):
        python_tokens(b"x = '\xff'\n")


# --- Formatting-only lines (D-3, reading R2, I3) -------------------------------------------

def test_reindenting_across_a_dedent_is_formatting_only() -> None:
    """I3: whole-version tokens; a hunk side read alone would fail to tokenise (I1)."""
    before = b"def f(x):\n    if x:\n        return 1\n    return 2\n"
    after = b"def f(x):\n    if x:\n        return 1 \n    return 2 \n"
    assert formatting_only_lines(change([Hunk(3, 2, 3, 2)]), before, after) == (4, 4)


def test_rewrapping_a_call_is_formatting_only() -> None:
    """Line breaks inside brackets are NL tokens, which D-3 ignores."""
    before = b"value = compute(a, b)\n"
    after = b"value = compute(\n    a, b)\n"
    assert formatting_only_lines(change([Hunk(1, 1, 1, 2)]), before, after) == (3, 3)


def test_an_edit_inside_a_docstring_is_not_formatting_only() -> None:
    """I3: the docstring overlaps the hunk on both sides and differs (I2 would miss it)."""
    before = b'def f():\n    """First.\n\n    Middle line.\n    """\n'
    after = b'def f():\n    """First.\n\n    Middle text.\n    """\n'
    assert formatting_only_lines(change([Hunk(4, 1, 4, 1)]), before, after) == (0, 2)


@pytest.mark.parametrize(("before", "after"), [
    (b"x = 1  # old\n", b"x = 1  # new\n"),  # COMMENT is kept by D-3
    (b"x = 'a'\n", b'x = "a"\n'),  # another STRING token
    (b"x = 1\n", b"x = 2\n"),
])
def test_token_changes_are_not_formatting_only(before: bytes, after: bytes) -> None:
    """A comment, quote style or value change changes the token sequence."""
    assert formatting_only_lines(change([Hunk(1, 1, 1, 1)]), before, after) == (0, 2)


def test_inserted_blank_lines_are_formatting_only() -> None:
    """A pure insertion of blank lines adds only NL tokens."""
    before = b"x = 1\ny = 2\n"
    after = b"x = 1\n\n\ny = 2\n"
    assert formatting_only_lines(change([Hunk(1, 0, 2, 2)]), before, after) == (2, 2)


def test_a_carriage_return_file_maps_python_lines_to_git_lines() -> None:
    """Without LF, Git sees one line; every Python line's tokens belong to it."""
    before = b"x = 1\ry = 2\r"
    after = b"x = 1\ry  =  2\r"
    assert formatting_only_lines(change([Hunk(1, 1, 1, 1)]), before, after) == (2, 2)
    assert formatting_only_lines(change([Hunk(1, 1, 1, 1)]), before,
                                 b"x = 1\ry = 3\r") == (0, 2)


def test_unreadable_versions_binary_files_and_pure_renames_have_none() -> None:
    """R2: a decoding or tokenising failure never gives formatting-only lines."""
    hunk = [Hunk(1, 1, 1, 1)]
    assert formatting_only_lines(change(hunk), b"x = '\xff'\n", b"x = '\xff' \n") == (0, 2)
    assert formatting_only_lines(change(hunk), b'x = """\n', b'x = """ \n') == (0, 2)
    assert formatting_only_lines(change(hunk, binary=True), b"x\n", b"y\n") == (0, 0)
    assert formatting_only_lines(change([], "a.py", "b.py", kind=ChangeType.RENAMED),
                                 b"x\n", b"x\n") == (0, 0)


def test_added_and_deleted_files_compare_against_nothing() -> None:
    """A side that does not exist has no tokens."""
    assert formatting_only_lines(change([Hunk(0, 0, 1, 1)], old=None, kind=ChangeType.ADDED),
                                 None, b"x = 1\n") == (0, 1)
    assert formatting_only_lines(change([Hunk(1, 1, 0, 0)], new=None,
                                        kind=ChangeType.DELETED), b"\n", None) == (1, 1)


# --- Formatting sweep (a2: D-13, reading R1, I1) -------------------------------------------

NARROW, WIDE = b"VALUES = [1, 2, 3]\n", b"VALUES = [\n    1,\n    2,\n    3]\n"


class Blobs(dict):
    """Blob contents by name, recording each read."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__()
        self.reads: list[str] = []

    def read(self, blob: str) -> bytes:
        """Return a blob and record the read."""
        self.reads.append(blob)
        return self[blob]


def reformatted(blobs: Blobs, path: str) -> FileChange:
    """Return a .py change whose 5 changed lines are all formatting-only."""
    item = change([Hunk(1, 1, 1, 4)], path, path)
    blobs[item.old.blob], blobs[item.new.blob] = NARROW, WIDE
    return item


def sweep(blobs: Blobs, count: int) -> list[FileChange]:
    """Return ``count`` reformatted .py files."""
    return [reformatted(blobs, f"pkg/f{index:02d}.py") for index in range(count)]


def test_more_than_20_qualifying_files_make_a_sweep_whatever_else_changes() -> None:
    """I1: 21 formatting-only .py files and a README edit give a2 (I2a would not)."""
    blobs = Blobs()
    readme = change([Hunk(1, 1, 1, 1)], "README.md", "README.md")
    assert formatting_sweep(sweep(blobs, 21) + [readme], blobs.read)
    assert readme.old.blob not in blobs.reads  # a non-.py file is never read


def test_a_sweep_that_also_fixes_a_file_is_still_a_sweep() -> None:
    """I1: the fixing file need not qualify itself (bulk-and-fix is bulk, RE-11)."""
    blobs = Blobs()
    fix = change([Hunk(1, 1, 1, 1)], "pkg/fix.py", "pkg/fix.py")
    blobs[fix.old.blob], blobs[fix.new.blob] = b'x = f"a"\n', b'x = "a"\n'
    assert formatting_sweep(sweep(blobs, 21) + [fix], blobs.read)


def test_twenty_files_are_not_more_than_twenty() -> None:
    """D-13: more than 20, so 20 qualifying files are no sweep."""
    blobs = Blobs()
    assert not formatting_sweep(sweep(blobs, 20), blobs.read)


def test_ninety_percent_boundary_exactly() -> None:
    """10 x formatting-only > 9 x changed: 9 of 10 fails, 10 of 11 passes."""
    blobs = Blobs()

    def file_with(formatting: int, other: int, name: str) -> FileChange:
        before = b"x = 1\n" + b"y = 1\n" * other
        after = b"x = 1\n" + b"\n" * formatting
        hunks = [Hunk(1, 0, 2, formatting)] + ([Hunk(2, other, 1, 0)] if other else [])
        item = change(hunks, name, name)
        blobs[item.old.blob], blobs[item.new.blob] = before, after
        return item

    nine = [file_with(9, 1, f"pkg/n{index:02d}.py") for index in range(21)]
    ten = [file_with(10, 1, f"pkg/t{index:02d}.py") for index in range(21)]
    assert formatting_only_lines(nine[0], blobs[nine[0].old.blob],
                                 blobs[nine[0].new.blob]) == (9, 10)
    assert not formatting_sweep(nine, blobs.read)
    assert formatting_sweep(ten, blobs.read)


def test_renames_binary_files_and_files_without_changed_lines() -> None:
    """Pure renames and binary changes never qualify; a renamed file with edits can."""
    blobs = Blobs()
    pure = [change([], f"pkg/r{index}.py", f"pkg/s{index}.py", kind=ChangeType.RENAMED)
            for index in range(5)]
    binary = [change([], f"pkg/b{index}.py", f"pkg/b{index}.py", binary=True)
              for index in range(5)]
    assert not formatting_sweep(sweep(blobs, 20) + pure + binary, blobs.read)
    moved = change([Hunk(1, 1, 1, 4)], "pkg/old.py", "pkg/new.py", kind=ChangeType.RENAMED)
    blobs[moved.old.blob], blobs[moved.new.blob] = NARROW, WIDE
    assert formatting_sweep(sweep(blobs, 20) + [moved], blobs.read)


def test_changed_lines_are_removed_plus_added_lines() -> None:
    """A hunk removing 1 line and adding 4 changes 5 lines."""
    item = change([Hunk(1, 1, 1, 4)])
    assert formatting_only_lines(item, NARROW, WIDE) == (5, 5)


# --- Line touch (D-5, reading P2) ----------------------------------------------------------

@pytest.mark.parametrize(("hunk", "exact", "near"), [
    (Hunk(10, 1, 10, 1), True, True),
    (Hunk(8, 3, 8, 0), True, True),  # removes 8-10
    (Hunk(10, 3, 9, 0), True, True),  # removes 10-12
    (Hunk(11, 1, 11, 1), False, True),
    (Hunk(13, 2, 13, 0), False, True),  # 3 lines away
    (Hunk(14, 1, 14, 1), False, False),  # 4 lines away
    (Hunk(6, 1, 6, 1), False, False),  # 4 lines away
    (Hunk(7, 1, 7, 1), False, True),  # 3 lines away
    (Hunk(10, 0, 11, 2), False, False),  # pure insertion after the line
    (Hunk(9, 0, 10, 2), False, False),  # pure insertion before the line
])
def test_exact_and_near_touch(hunk: Hunk, exact: bool, near: bool) -> None:
    """Exact: the line is removed; near: a removed line within 3 lines; insertions never."""
    touch = line_touch([hunk], 10, (5, 20))
    assert (touch.exact, touch.near) == (exact, near)


@pytest.mark.parametrize(("hunk", "scope"), [
    (Hunk(12, 1, 12, 1), True),  # a removed line inside
    (Hunk(4, 1, 4, 1), False),  # just before the scope
    (Hunk(21, 1, 21, 1), False),  # just after it
    (Hunk(3, 3, 3, 0), True),  # a removed range reaching into it
    (Hunk(7, 0, 8, 1), True),  # a pure insertion between two of its lines
    (Hunk(20, 0, 21, 1), False),  # a pure insertion after its last line
    (Hunk(4, 0, 5, 1), False),  # a pure insertion before its first line
])
def test_scope_touch(hunk: Hunk, scope: bool) -> None:
    """Scope: a removed line inside, or a pure insertion between two of its lines."""
    assert line_touch([hunk], 30, (5, 20)).scope is scope


def test_module_level_scope_touch_is_any_hunk_in_the_file() -> None:
    """A module-level item's scope is the whole file."""
    assert line_touch([Hunk(90, 0, 91, 1)], 3, None) == LineTouch(False, False, True)
    assert line_touch([], 3, None) == LineTouch(False, False, False)


def test_scope_lines_use_gits_numbering() -> None:
    """A scope's Python lines are mapped to Git's lines (a lone CR joins Git lines)."""
    source = b"x = 1\ry = 2\ndef f():\n    return 1\n"
    assert git_span(source, span_of(source, "f")) == (2, 3)
    assert git_span(b"def f():\n    return 1\n", ScopeSpan("f", FUNCTION, 1, 5)) == (1, 5)


def test_a_renamed_scope_at_exactly_0_8_is_a_counterpart() -> None:
    """D-1: "similarity >= 0.8", so exactly 0.8 is found; just below would not be."""
    before = b'def f(a):\n    return f"x", a\n'
    after = b'def g(a):\n    return "x", a\n'
    found = counterpart_of(before, span_of(before, "f"), after, ("m.py", "m.py", "c1"))
    assert found == Counterpart(MOST_SIMILAR, "g", 0.8)
    lower = b'def g(b):\n    return "x", a\n'
    assert counterpart_of(before, span_of(before, "f"), lower,
                          ("m.py", "m.py", "c1")) == Counterpart()


def test_autojunk_is_off_for_long_scopes() -> None:
    """D-2: autojunk off; on a scope of 200 or more tokens it would change the ratio."""
    before = "def f():\n" + "".join(f"    x{index} = (1, 2)\n" for index in range(40))
    after = "def f():\n" + "".join(f"    y{index} = (1, 2)\n" for index in range(40))
    before, after = before.encode(), after.encode()  # same layout, every name renamed
    own = scope_tokens(python_tokens(before), span_of(before, "f"))
    other = scope_tokens(python_tokens(after), span_of(after, "f"))
    assert len(own) >= 200
    assert similarity(own, other) == SequenceMatcher(None, own, other, autojunk=False).ratio()
    assert similarity(own, other) > 0.8 > 0.1 > SequenceMatcher(None, own, other).ratio()
