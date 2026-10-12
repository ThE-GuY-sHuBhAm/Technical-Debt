"""RE-10 (FR-55): tracking, censoring and time-to-event, on FX-RG1 v2 and on hand-made inputs.

Component checks on FX-RG1 v2 (PASS as components): items A to D are the four items RE-10
names, taken from their introducing commits by FR-53 conditions (1) and (2) (new, on an
added line), and followed with the real history reader, research profile and matcher:
W = 6 and the fixture's cut-off. Whether a run introduces them depends on CL-03 (bot
status, package filter), so RE-10 run as written, "1) Run with W = 6", stays BLOCKED:
a run is refused while CL-03 is incomplete (strict expected failure at the end).

The hand-made checks cover FR-55's other rules and the readings the module states
(month ends, rounding, which commit ends following, binary diffs), with an in-memory
source; they are mechanics, not RE-10 evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import make_fx_rg1_v2
import pytest
from fx_rg1_clone import ClonedFxRg1V2
from make_fx_rg1_v2 import CUTOFF, ITEM_LINE, ITEMS, SYMBOL, W_MONTHS

from tdea.analyzer.research.engine import ResearchEngine, RulesIncomplete
from tdea.analyzer.research.profile import (FAILED, OK, SYNTAX_ERROR, LintMessage,
                                            LintResult, ResearchProfile)
from tdea.analyzer.research.rg1.introduction import (FileVersions, Introduction,
                                                     IntroductionDetector)
from tdea.analyzer.research.rg1.matching import ItemKey
from tdea.analyzer.research.rg1.package_filter import PackageFilter
from tdea.analyzer.research.rg1.tracking import (CUT_OFF, WINDOW, ChainCommit, ItemTracker,
                                                 Tracking, TrackingStopped,
                                                 add_calendar_months, chain_of, days_between)
from tdea.analyzer.research.rg1.unit_run import HistorySource
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk

UTC = timezone.utc
INTRODUCING = {"A": "a_intro", "B": "b_intro", "C": "c_intro", "D": "d_intro"}


def introduction_of(cloned: ClonedFxRg1V2, item: str) -> Introduction:
    """Return item A, B, C or D as FR-53 records it at its introducing commit.

    Conditions (1) and (2) are applied by IntroductionDetector.candidates(); conditions
    (3) to (5) (selection, package code, not a bot commit) are a run's and CL-03's.
    """
    commit = cloned.commit(INTRODUCING[item])
    path = ITEMS[item][0]
    (change,) = [change for change in cloned.reader.file_changes(commit)
                 if change.new is not None and change.new.path == path]
    content = cloned.reader.read_blob(change.new.blob)
    lint = ResearchProfile().lint(content, Path(path).name)
    versions = FileVersions(change, None, lint, b"", content)
    (occurrence,) = IntroductionDetector(PackageFilter()).candidates(versions)
    message = occurrence.message
    return Introduction(commit.sha, commit.is_merge, commit.committed_at, path,
                        change.new.blob, occurrence.key, message.message_id,
                        message.category, message.message, message.line, occurrence.index)


@pytest.fixture(name="tracked", scope="module")
def _tracked(cloned_fx_rg1_v2: ClonedFxRg1V2) -> dict[str, Tracking]:
    """Track A to D through FX-RG1 v2 with W = 6 and the fixture's cut-off."""
    source = HistorySource(cloned_fx_rg1_v2.reader, ResearchProfile(), None)
    tracker = ItemTracker(source)
    chain = chain_of(cloned_fx_rg1_v2.history)
    return {item: tracker.track(introduction_of(cloned_fx_rg1_v2, item), chain, W_MONTHS,
                                CUTOFF) for item in INTRODUCING}


def test_the_items_are_the_fixture_items(cloned_fx_rg1_v2: ClonedFxRg1V2) -> None:
    """A to D are W1309 items on line 6 of their modules, with the expected keys."""
    for item, (path, scope, text) in ITEMS.items():
        introduction = introduction_of(cloned_fx_rg1_v2, item)
        assert (introduction.path, introduction.line) == (path, ITEM_LINE)
        assert introduction.key == ItemKey(SYMBOL, text, scope)


def test_re10_a_c1_is_found_after_the_rename_with_the_right_time_to_event(
    cloned_fx_rg1_v2: ClonedFxRg1V2, tracked: dict[str, Tracking]
) -> None:
    """A survives the rename commit and disappears at the later fix; 33 d 6 h 30 min 27 s."""
    shas = cloned_fx_rg1_v2.fixture.shas
    a = tracked["A"]
    assert a.c1_sha == shas["a_fix"] != shas["a_rename"]
    assert a.c1_date == datetime(2026, 5, 11, 15, 30, 27, tzinfo=UTC)
    assert a.introduced_at == datetime(2026, 4, 8, 9, 0, tzinfo=UTC)
    assert a.time_to_event_days == 33.2711  # 33 + 23427 / 86400 = 33.27114...
    assert (a.censoring_date, a.censoring_reason, a.clock_anomaly) == (None, None, False)
    assert a.path_before_c1 == a.path_after_c1 == "pkg/tracked_alpha.py"
    rename = [change for change in cloned_fx_rg1_v2.reader.file_changes(shas["a_rename"])
              if change.change_type is ChangeType.RENAMED]
    assert [change.old.path for change in rename] == ["pkg/tracked_a.py"]
    assert a.blob_before_c1 == rename[0].new.blob
    (fix,) = cloned_fx_rg1_v2.reader.file_changes(shas["a_fix"])
    assert a.blob_after_c1 == fix.new.blob
    assert (a.line_before_c1, a.text_before_c1) == (ITEM_LINE, '    return f"hello"')


def test_re10_b_is_censored_at_the_end_of_w(cloned_fx_rg1_v2: ClonedFxRg1V2,
                                            tracked: dict[str, Tracking]) -> None:
    """B persists past W = 6: censored "window" at introduction + 6 months, though fixed later."""
    b = tracked["B"]
    assert (b.censoring_reason, b.censoring_date) == (
        WINDOW, datetime(2026, 10, 9, 9, 0, tzinfo=UTC))
    assert b.c1_sha is None and b.time_to_event_days is None
    b_fix = cloned_fx_rg1_v2.commit("b_fix")  # in the history, but after the end of W
    assert datetime(2026, 10, 9, 9, 0, tzinfo=UTC) < b_fix.committed_at <= CUTOFF


def test_re10_c_is_censored_at_the_cut_off_before_w_ends(cloned_fx_rg1_v2: ClonedFxRg1V2,
                                                         tracked: dict[str, Tracking]) -> None:
    """C persists to the cut-off, which comes before its W ends: censored "cut-off"."""
    c = tracked["C"]
    assert (c.censoring_reason, c.censoring_date) == (CUT_OFF, CUTOFF)
    assert c.c1_sha is None
    excluded = [commit.sha for commit in cloned_fx_rg1_v2.history.excluded]
    assert excluded == [cloned_fx_rg1_v2.fixture.shas["c_fix"]]


def test_re10_d_keeps_a_negative_interval_and_the_clock_anomaly_flag(
    cloned_fx_rg1_v2: ClonedFxRg1V2, tracked: dict[str, Tracking]
) -> None:
    """D's c1 is dated 7 days before D's introduction: -7.0 days, kept and flagged."""
    d = tracked["D"]
    assert d.c1_sha == cloned_fx_rg1_v2.fixture.shas["d_fix"]
    assert d.time_to_event_days == -7.0 and d.clock_anomaly
    assert d.c1_date < d.introduced_at


def test_re10_b_would_end_at_its_fix_with_a_longer_w(cloned_fx_rg1_v2: ClonedFxRg1V2) -> None:
    """With W = 7, B's fix lies inside W, so W = 6 is what censors B (not the history)."""
    source = HistorySource(cloned_fx_rg1_v2.reader, ResearchProfile(), None)
    b = ItemTracker(source).track(introduction_of(cloned_fx_rg1_v2, "B"),
                                  chain_of(cloned_fx_rg1_v2.history), 7, CUTOFF)
    assert b.c1_sha == cloned_fx_rg1_v2.fixture.shas["b_fix"]


def test_bisection_does_not_exist() -> None:
    """RE-10's bisection step does not apply: the linear scan is the only one (Freeze 12)."""
    import tdea.analyzer.research.rg1.tracking as tracking  # pylint: disable=import-outside-toplevel
    assert not [name for name in dir(tracking) if "bisect" in name.lower()]


@pytest.mark.xfail(strict=True, raises=RulesIncomplete,
                   reason="BLOCKED: RE-10 step 1 runs FX-RG1 with W = 6, and a run is "
                          "refused while CL-03 is incomplete (Task 7 Day 6 decision 1)")
def test_re10_as_a_run_with_w_6() -> None:
    """RE-10 step 1 as written: a run of FX-RG1. The real engine refuses before any work."""
    ResearchEngine().check_rules()


# --- mechanics on hand-made inputs (not RE-10 evidence) ----------------------------------

T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
FAR = datetime(2030, 1, 1, tzinfo=UTC)
PATH = "pkg/m.py"
W1309 = LintMessage(SYMBOL, "W1309", "warning", "Using an f-string", 2, "run")
SOURCE = b'def run():\n    return f"x"\n'
CLEAN = b'def run():\n    return "x"\n'


@dataclass
class MemorySource:
    """An in-memory TrackingSource: changes per commit, blobs, lint results per blob."""

    changes: dict[str, tuple[FileChange, ...]] = field(default_factory=dict)
    blobs: dict[str, bytes] = field(default_factory=dict)
    lints: dict[str, LintResult] = field(default_factory=dict)

    def file_changes(self, sha: str) -> tuple[FileChange, ...]:
        """Return the changes recorded for ``sha``."""
        return self.changes.get(sha, ())

    def read_blob(self, blob: str) -> bytes:
        """Return a blob's bytes."""
        return self.blobs[blob]

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Return the lint result recorded for ``blob``."""
        assert self.blobs[blob] == content and path
        return self.lints[blob]


def sha(number: int) -> str:
    """Return a SHA for commit number ``number``."""
    return f"{number:040x}"


def blob(name: str) -> str:
    """Return a blob ID for a version name."""
    return name.encode().hex().ljust(40, "0")[:40]


def started(content: bytes = SOURCE, lint: LintResult | None = None) -> tuple[MemorySource,
                                                                             Introduction]:
    """Return a source holding version v0 and the item introduced in it at commit 1."""
    source = MemorySource()
    source.blobs[blob("v0")] = content
    source.lints[blob("v0")] = lint or LintResult(OK, None, (W1309,))
    introduction = Introduction(sha(1), False, T0, PATH, blob("v0"),
                                ItemKey(SYMBOL, 'return f"x"', "run"), "W1309", "warning",
                                "Using an f-string", 2, 0)
    return source, introduction


def modify(source: MemorySource, number: int, old: str, new: str, content: bytes | None,
           lint: LintResult | None, hunks: tuple[Hunk, ...] = (), binary: bool = False,
           new_path: str = PATH, old_path: str = PATH) -> None:
    """Record commit ``number`` changing the file from version ``old`` to ``new``."""
    new_side = None
    if content is not None:
        source.blobs[blob(new)] = content
        source.lints[blob(new)] = lint
        new_side = FileSide(new_path, blob(new))
    kind = (ChangeType.DELETED if new_side is None else
            ChangeType.RENAMED if new_path != old_path else ChangeType.MODIFIED)
    source.changes[sha(number)] = (FileChange(kind, FileSide(old_path, blob(old)), new_side,
                                              90 if kind is ChangeType.RENAMED else None,
                                              binary, hunks),)


def chain(*dates: datetime, cut_after: int | None = None) -> tuple[ChainCommit, ...]:
    """Return commits 1.. with the given dates; positions from ``cut_after`` are excluded."""
    return tuple(ChainCommit(sha(index), index, moment,
                             cut_after is not None and index > cut_after)
                 for index, moment in enumerate(dates, start=1))


def days(count: float) -> datetime:
    """Return T0 + ``count`` days."""
    return T0 + timedelta(days=count)


def test_a_deleted_file_ends_the_match() -> None:
    """The file removed at commit 3: c1 = 3, no path or blob after c1."""
    source, introduction = started()
    modify(source, 3, "v0", "gone", None, None, (Hunk(1, 2, 0, 0),))
    result = ItemTracker(source).track(introduction, chain(T0, days(1), days(2)), 6, FAR)
    assert (result.c1_sha, result.time_to_event_days) == (sha(3), 2.0)
    assert (result.path_after_c1, result.blob_after_c1) == (None, None)
    assert (result.path_before_c1, result.blob_before_c1) == (PATH, blob("v0"))


def test_a_lint_failure_ends_the_match_even_on_a_binary_diff() -> None:
    """A version that fails to lint ends the match (FR-55) without needing hunks."""
    source, introduction = started()
    modify(source, 2, "v0", "broken", b"def run(:\n", LintResult(FAILED, SYNTAX_ERROR, ()),
           binary=True)
    result = ItemTracker(source).track(introduction, chain(T0, days(3)), 6, FAR)
    assert result.c1_sha == sha(2) and result.blob_after_c1 == blob("broken")


def test_a_binary_diff_that_still_lints_stops_tracking() -> None:
    """No hunks, a linting version: FR-53 cannot be applied, so tracking stops, explicitly."""
    source, introduction = started()
    modify(source, 2, "v0", "v1", SOURCE + b"# x\n", LintResult(OK, None, (W1309,)),
           binary=True)
    with pytest.raises(TrackingStopped, match="binary") as caught:
        ItemTracker(source).track(introduction, chain(T0, days(3)), 6, FAR)
    assert sha(2) in caught.value.cause and caught.value.action


def test_untouched_commits_are_skipped_and_a_moved_item_is_followed() -> None:
    """Commit 2 does not touch the file; commit 3 inserts 2 lines above; commit 4 fixes it."""
    source, introduction = started()
    moved = b"import os\nimport sys\n" + SOURCE
    modify(source, 3, "v0", "v1", moved, LintResult(OK, None, (replace(W1309, line=4),)),
           (Hunk(0, 0, 1, 2),))
    modify(source, 4, "v1", "v2", b"import os\nimport sys\n" + CLEAN, LintResult(OK, None, ()),
           (Hunk(4, 1, 4, 1),))
    result = ItemTracker(source).track(introduction, chain(T0, days(1), days(2), days(3)),
                                       6, FAR)
    assert (result.c1_sha, result.line_before_c1, result.text_before_c1) == (
        sha(4), 4, '    return f"x"')
    assert result.blob_before_c1 == blob("v1")


def test_a_rename_with_the_item_unchanged_is_followed() -> None:
    """A rename keeps the item; the fix after it is c1, with the new path before and after."""
    source, introduction = started()
    modify(source, 2, "v0", "v1", SOURCE, LintResult(OK, None, (W1309,)), (),
           new_path="pkg/n.py")
    modify(source, 3, "v1", "v2", CLEAN, LintResult(OK, None, ()), (Hunk(2, 1, 2, 1),),
           new_path="pkg/n.py", old_path="pkg/n.py")
    result = ItemTracker(source).track(introduction, chain(T0, days(1), days(2)), 6, FAR)
    assert (result.c1_sha, result.path_before_c1, result.path_after_c1) == (
        sha(3), "pkg/n.py", "pkg/n.py")


def test_following_ends_at_the_first_commit_after_the_end_of_w() -> None:
    """Any commit dated after the end of W ends following, even one not touching the file."""
    source, introduction = started()
    modify(source, 3, "v0", "v1", CLEAN, LintResult(OK, None, ()), (Hunk(2, 1, 2, 1),))
    skewed = chain(T0, add_calendar_months(T0, 1) + timedelta(seconds=1), days(5))
    result = ItemTracker(source).track(introduction, skewed, 1, FAR)
    assert (result.c1_sha, result.censoring_reason) == (None, WINDOW)
    assert result.censoring_date == datetime(2026, 2, 10, 12, 0, tzinfo=UTC)


def test_a_commit_dated_exactly_at_the_end_of_w_is_still_examined() -> None:
    """"After" is strict: a fix dated exactly at the end of W is c1."""
    source, introduction = started()
    modify(source, 2, "v0", "v1", CLEAN, LintResult(OK, None, ()), (Hunk(2, 1, 2, 1),))
    end = add_calendar_months(T0, 1)
    result = ItemTracker(source).track(introduction, chain(T0, end), 1, FAR)
    assert result.c1_sha == sha(2) and result.time_to_event_days == 31.0


def test_the_first_commit_after_the_cut_off_ends_following() -> None:
    """A position left out by the cut-off ends following; a later skewed commit is not used."""
    source, introduction = started()
    modify(source, 3, "v0", "v1", CLEAN, LintResult(OK, None, ()), (Hunk(2, 1, 2, 1),))
    cutoff = days(10)
    entries = chain(T0, days(20), days(4), cut_after=1)
    entries = (entries[0], entries[1], replace(entries[2], after_cutoff=False))
    result = ItemTracker(source).track(introduction, entries, 6, cutoff)
    assert (result.c1_sha, result.censoring_reason, result.censoring_date) == (
        None, CUT_OFF, cutoff)


def test_a_tie_between_the_end_of_w_and_the_cut_off_is_window() -> None:
    """When W ends exactly at the cut-off, the reason is "window" (reading, RE-10 wording)."""
    source, introduction = started()
    end = add_calendar_months(T0, 2)
    result = ItemTracker(source).track(introduction, chain(T0), 2, end)
    assert (result.censoring_reason, result.censoring_date) == (WINDOW, end)
    result = ItemTracker(source).track(introduction, chain(T0), 2,
                                       end - timedelta(seconds=1))
    assert result.censoring_reason == CUT_OFF


def test_an_inconsistent_history_is_an_error_not_a_guess() -> None:
    """A change whose old blob is not the tracked version means the chain is out of order."""
    source, introduction = started()
    modify(source, 2, "other", "v1", CLEAN, LintResult(OK, None, ()), (Hunk(2, 1, 2, 1),))
    with pytest.raises(RuntimeError, match="out of order"):
        ItemTracker(source).track(introduction, chain(T0, days(1)), 6, FAR)


def test_w_must_be_positive_and_the_item_must_be_in_its_version() -> None:
    """W = 0 and an item missing from its own version are programming errors."""
    source, introduction = started()
    with pytest.raises(ValueError):
        ItemTracker(source).track(introduction, chain(T0), 0, FAR)
    source.lints[blob("v0")] = LintResult(OK, None, ())
    with pytest.raises(RuntimeError):
        ItemTracker(source).track(introduction, chain(T0), 6, FAR)
    with pytest.raises(ValueError):
        ItemTracker(MemorySource()).track(introduction, (), 6, FAR)


@pytest.mark.parametrize("start, months, end", [
    (datetime(2026, 8, 31, 9, 15, tzinfo=UTC), 6, datetime(2027, 2, 28, 9, 15, tzinfo=UTC)),
    (datetime(2027, 8, 31, tzinfo=UTC), 6, datetime(2028, 2, 29, tzinfo=UTC)),
    (datetime(2026, 1, 31, tzinfo=UTC), 1, datetime(2026, 2, 28, tzinfo=UTC)),
    (datetime(2026, 12, 15, tzinfo=UTC), 1, datetime(2027, 1, 15, tzinfo=UTC)),
    (datetime(2026, 3, 31, tzinfo=UTC), 12, datetime(2027, 3, 31, tzinfo=UTC)),
    (datetime(2026, 5, 31, tzinfo=UTC), 30, datetime(2028, 11, 30, tzinfo=UTC)),
])
def test_calendar_months_clamp_to_the_month_end(start: datetime, months: int,
                                                end: datetime) -> None:
    """Reading: the day is kept, a missing day becomes the month's last; time is kept."""
    assert add_calendar_months(start, months) == end


@pytest.mark.parametrize("seconds, expected", [
    (86_400, 1.0), (108, 0.0012), (324, 0.0038), (540, 0.0062), (-108, -0.0012),
    (23_427 + 33 * 86_400, 33.2711), (0, 0.0), (43_200, 0.5), (1, 0.0),
])
def test_days_are_rounded_half_to_even_from_the_exact_interval(seconds: int,
                                                               expected: float) -> None:
    """Reading: 4 decimals, half to even (108 s = 0.00125 d -> 0.0012; 324 s -> 0.0038)."""
    assert days_between(T0, T0 + timedelta(seconds=seconds)) == expected
