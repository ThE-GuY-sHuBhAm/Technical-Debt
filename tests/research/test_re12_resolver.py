"""RE-12 (FR-56): resolver, same-account flags and the base rate (reading R5).

SYNTHETIC — not CL-03. Provenance comes from run_support's SyntheticClassifier, which
labels commits by subject and gives a made-up tool name; no CL-03 pattern or rule is
used. RE-12 itself names a synthetic history, but its "AI-traced" commit must be
classified by FR-54, which CL-03 blocks, so RE-12 as written stays BLOCKED (strict
expected failure at the end). These are mechanics checks, not scientific validation.

The ten base-rate checks of the R5 reconciliation each tell the approved reading apart
from an alternative. Checks 1-3, 7 and 10 run real git histories through the engine, so
c1, renames and censoring are real; checks 4, 5, 8 and 9 use hand-made records; check 6
(corrected) runs a history with a commit excluded by the cut-off, showing that the base
rate never reads it while Day 6 tracking keeps its own stop rule.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fixture_git import FixtureRepo
from handmade import Entry, Record, provenance
from make_fx_rg1 import ASHA, JANE, RAVI
from run_support import (FX_RG1_URL, NOW, LocalAccess, SyntheticBulkRules,
                         SyntheticClassifier, SyntheticPackages)

from tdea.analyzer.research.engine import ResearchEngine, Rg1Rules, RulesIncomplete
from tdea.analyzer.research.rg1.base_rate import BaseRate, base_rate
from tdea.analyzer.research.rg1.fail_closed import ClassificationStopped
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import ItemKey
from tdea.analyzer.research.rg1.provenance import TraceStatus
from tdea.analyzer.research.rg1.tracking import CUT_OFF
from tdea.analyzer.research.rg1.unit_run import (RepositoryPlan, RepositorySession, TrackedItem,
                                                 UnitWindow)
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk

UTC = timezone.utc
AI, BOT, NON = TraceStatus.AI_TRACED, TraceStatus.BOT, TraceStatus.NON_TRACED
TOOL = "SYNTHETIC tool A"


def item_module(name: str) -> str:
    """Return a package module whose function returns an f-string without interpolation."""
    return f'"""Module {name}."""\n\n\ndef {name}():\n    """Return a value."""\n' \
           f'    return f"{name}"\n'


def fixed(name: str) -> str:
    """Return the module with its warning fixed."""
    return item_module(name).replace(f'f"{name}"', f'"{name}"')


def edited(text: str, count: int) -> str:
    """Return ``text`` with ``count`` constant lines appended (no new warning)."""
    return text + "".join(f"VALUE_{index} = {index}\n" for index in range(count))


@dataclass
class Session:
    """A clone of a synthetic history, read through the engine (SYNTHETIC — not CL-03)."""

    engine: ResearchEngine
    session: RepositorySession

    def unit(self, start: str, end: str, w_months: int = 6) -> dict[str, TrackedItem]:
        """Run one unit and return its items by path."""
        outcome = self.engine.run_rg1_unit(self.session, UnitWindow(
            datetime.fromisoformat(start), datetime.fromisoformat(end)), w_months)
        return {item.introduction.path: item for item in outcome.items}


@pytest.fixture(name="read")
def _read(tmp_path: Path) -> Iterator:
    """Yield a function that reads a FixtureRepo through the engine with given labels."""
    opened: list[RepositorySession] = []

    def read(repo: FixtureRepo, labelled: dict[str, TraceStatus], cutoff: datetime,
             tools: dict[str, str] | None = None) -> Session:
        rules = Rg1Rules(SyntheticClassifier(labelled, tools),
                         SyntheticPackages([f"pkg/{name}.py" for name in PACKAGE_NAMES]),
                         SyntheticBulkRules(()))
        engine = ResearchEngine(rules, None, LocalAccess({"tdea-fixtures/fx-rg1": repo.path}),
                                clock=lambda: NOW)
        clones = tmp_path / f"clones-{len(opened)}"
        clones.mkdir()
        session = engine.open_repository(RepositoryPlan(FX_RG1_URL, None, cutoff), key=KEY,
                                         clone_root=clones)
        opened.append(session)
        return Session(engine, session)

    yield read
    for session in opened:
        session.close()


KEY = bytes(range(32))
PACKAGE_NAMES = ("feature", "post", "ren", "ren2", "dele", "bot", "bot2", "zero", "cens",
                 "six")


# --- RE-12 --------------------------------------------------------------------------------

def re12_history(path: Path) -> FixtureRepo:
    """RE-12's synthetic history: A's item, fixed by an AI-traced commit from A's account."""
    repo = FixtureRepo(path)
    text = item_module("feature")
    repo.commit("Add the feature", {"pkg/feature.py": text}, author=ASHA,
                date="2026-01-05T09:00:00+00:00")
    text = fixed("feature")
    repo.commit("Fix the feature with an assistant", {"pkg/feature.py": text}, author=ASHA,
                date="2026-01-20T09:00:00+00:00")
    later = [("AI edit 2", "02-01"), ("Edit 1", "03-01"), ("AI edit 3", "03-15"),
             ("Edit 2", "04-01"), ("Bot edit", "04-15"), ("Edit 3", "05-01"),
             ("Edit 4", "06-01"), ("Edit 5", "07-01"),  # all within W (ends 5 Jul)
             ("Edit after W 1", "08-01"), ("Edit after W 2", "09-01")]
    for index, (subject, day) in enumerate(later, start=1):
        repo.commit(subject, {"pkg/feature.py": text + f"STEP = {index}\n"}, author=RAVI,
                    date=f"2026-{day}T09:00:00+00:00")
    return repo


RE12_LABELS = {"Fix the feature with an assistant": AI, "AI edit 2": AI, "AI edit 3": AI,
               "Bot edit": BOT}


def test_synthetic_re12_resolver_flags_and_base_rate(read, tmp_path: Path) -> None:
    """SYNTHETIC — not CL-03: AI-traced resolver with its tool, both flags, 3 and 5, 0.375."""
    found = read(re12_history(tmp_path / "re12"), RE12_LABELS,
                 datetime(2026, 10, 1, tzinfo=UTC),
                 {"Fix the feature with an assistant": TOOL}).unit(
        "2026-01-01T00:00:00+00:00", "2026-01-10T00:00:00+00:00")
    item = found["pkg/feature.py"]
    assert item.disappearance is not None
    assert (item.disappearance.resolver_trace_status, item.disappearance.resolver_tool) == (
        "ai_traced", TOOL)
    assert (item.disappearance.same_name, item.disappearance.same_email) == (True, True)
    assert item.base_rate == BaseRate(3, 5, 0.375, datetime(2026, 7, 5, 9, 0, tzinfo=UTC))


@pytest.mark.xfail(strict=True, raises=RulesIncomplete,
                   reason="BLOCKED: CL-03. The real FR-54 classifier has no matching rule, so "
                          "no commit can be classified and the resolver cannot be AI-traced")
def test_re12_as_written_with_the_real_classifier(tmp_path: Path) -> None:
    """RE-12: the resolver is AI-traced by FR-54's own rules."""
    repo = re12_history(tmp_path / "re12")
    engine = ResearchEngine(Rg1Rules(), None, LocalAccess({"tdea-fixtures/fx-rg1": repo.path}),
                            clock=lambda: NOW)
    clones = tmp_path / "clones"
    clones.mkdir()
    session = engine.open_repository(
        RepositoryPlan(FX_RG1_URL, None, datetime(2026, 10, 1, tzinfo=UTC)), key=KEY,
        clone_root=clones)
    try:
        outcome = engine.run_rg1_unit(session, UnitWindow(
            datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)), 6)
    finally:
        session.close()
    (item,) = outcome.items
    assert item.disappearance is not None
    assert item.disappearance.resolver_trace_status == "ai_traced"


# --- Base rate: checks 1-3, 7 and 10 on one synthetic history (R5) ------------------------

@pytest.fixture(name="history_items")
def _history_items(read, tmp_path: Path) -> dict[str, TrackedItem]:
    """Items post, ren, dele, bot, zero and cens of one history, with W = 6."""
    repo = FixtureRepo(tmp_path / "rates")
    names = ("post", "ren", "dele", "bot", "zero", "cens")
    repo.commit("Add the modules", {f"pkg/{name}.py": item_module(name) for name in names},
                author=ASHA, date="2026-01-05T09:00:00+00:00")
    steps = [  # (subject, files, day of 2026, a rename to make first)
        ("Fix post", {"pkg/post.py": fixed("post")}, "01-14", None),
        ("Fix ren with an assistant", {"pkg/ren.py": fixed("ren")}, "01-15", None),
        ("Fix dele", {"pkg/dele.py": fixed("dele")}, "01-16", None),
        ("Bot fixes bot", {"pkg/bot.py": fixed("bot")}, "01-17", None),
        ("Bot touches zero", {"pkg/zero.py": edited(item_module("zero"), 1)}, "01-18", None),
        ("Edit post once", {"pkg/post.py": edited(fixed("post"), 1)}, "01-24", None),
        ("Edit cens once", {"pkg/cens.py": edited(item_module("cens"), 1)}, "01-25", None),
        ("Delete dele", {"pkg/dele.py": None}, "01-26", None),
        ("Edit post twice", {"pkg/post.py": edited(fixed("post"), 2)}, "02-03", None),
        ("Edit cens twice", {"pkg/cens.py": edited(item_module("cens"), 2)}, "02-04", None),
        ("Add dele again", {"pkg/dele.py": item_module("dele")}, "02-05", None),
        ("Edit dele again", {"pkg/dele.py": edited(item_module("dele"), 1)}, "02-15", None),
        ("Bot renames bot", {}, "02-16", ("pkg/bot.py", "pkg/bot2.py")),
        ("Edit bot2", {"pkg/bot2.py": edited(fixed("bot"), 1)}, "02-17", None),
        ("Rename ren", {}, "02-18", ("pkg/ren.py", "pkg/ren2.py")),
        ("Edit ren2", {"pkg/ren2.py": edited(fixed("ren"), 1)}, "02-19", None),
    ]
    for index, (subject, files, day, rename) in enumerate(steps):
        if rename is not None:
            repo.git("mv", *rename)
        repo.commit(subject, files, author=JANE if index % 2 else RAVI,
                    date=f"2026-{day}T09:00:00+00:00")
    labels = {"Fix ren with an assistant": AI, "Bot fixes bot": BOT, "Bot touches zero": BOT,
              "Bot renames bot": BOT}
    return read(repo, labels, datetime(2026, 4, 1, tzinfo=UTC)).unit(
        "2026-01-01T00:00:00+00:00", "2026-01-06T00:00:00+00:00")


END_OF_W = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)


def test_check1_counting_goes_on_after_c1(history_items: dict[str, TrackedItem]) -> None:
    """c1 and the two later touches count: 3 (stopping at c1 would give 1)."""
    item = history_items["pkg/post.py"]
    assert item.tracking.c1_sha is not None
    assert item.base_rate == BaseRate(0, 3, 0.0, END_OF_W)


def test_check2_renames_are_followed_after_c1(history_items: dict[str, TrackedItem]) -> None:
    """c1 (AI-traced), the rename and the edit of the renamed file: AI 1, non-traced 2."""
    assert history_items["pkg/ren.py"].base_rate == BaseRate(1, 2, 1 / 3, END_OF_W)


def test_check3_a_deletion_counts_and_a_readded_path_is_another_file(
    history_items: dict[str, TrackedItem]
) -> None:
    """c1 and the deletion count; the re-added path and its edit do not (path-following: 4)."""
    assert history_items["pkg/dele.py"].base_rate == BaseRate(0, 2, 0.0, END_OF_W)


def test_check7_bot_commits_are_not_counted_but_move_the_file(
    history_items: dict[str, TrackedItem]
) -> None:
    """c1 and the rename are bot commits: not counted; the edit after the rename is."""
    item = history_items["pkg/bot.py"]
    assert item.disappearance is not None and item.disappearance.resolver_trace_status == "bot"
    assert item.base_rate == BaseRate(0, 1, 0.0, END_OF_W)


def test_check9_only_bot_touches_give_a_null_share(history_items: dict[str, TrackedItem]) -> None:
    """Zero denominator: 0 and 0 with a null share, not 0.0."""
    rate = history_items["pkg/zero.py"].base_rate
    assert rate == BaseRate(0, 0, None, END_OF_W)
    assert rate is not None and rate.ai_traced_share is None


def test_check10_a_censored_item_has_a_base_rate_to_its_censoring_point(
    history_items: dict[str, TrackedItem]
) -> None:
    """Censored at the cut-off: two touches counted; window_end is the end of W, not the cut-off."""
    item = history_items["pkg/cens.py"]
    assert item.tracking.censoring_reason == CUT_OFF
    assert item.tracking.censoring_date == datetime(2026, 4, 1, tzinfo=UTC)
    assert item.base_rate == BaseRate(0, 2, 0.0, END_OF_W)
    assert item.base_rate.window_end != item.tracking.censoring_date


# --- Check 6 (corrected): a commit excluded by the cut-off --------------------------------

def test_check6_the_base_rate_never_reads_a_commit_excluded_by_the_cut_off(
    read, tmp_path: Path
) -> None:
    """D1: tracking stops at the excluded commit (Day 6, unchanged); the base rate skips it.

    X, dated after the cut-off, sits before Y in history order; Y is dated before the
    cut-off, touches the file and fixes the item. Tracking censors the item at the cut-off
    because its rule stops at the first excluded position; the base rate reads only the
    recorded history, never X, and counts Y. window_end stays the end of W.
    """
    repo = FixtureRepo(tmp_path / "six")
    repo.commit("Add six", {"pkg/six.py": item_module("six")}, author=ASHA,
                date="2026-01-05T09:00:00+00:00")
    repo.commit("X: dated after the cut-off", {"pkg/six.py": edited(item_module("six"), 1)},
                author=RAVI, date="2026-03-10T09:00:00+00:00")
    repo.commit("Y: an eligible later touch", {"pkg/six.py": edited(fixed("six"), 1)},
                author=JANE, date="2026-02-20T09:00:00+00:00")
    reader = read(repo, {}, datetime(2026, 3, 1, tzinfo=UTC))
    recorded = [commit.sha for commit in reader.session.record.commits]
    excluded = repo.git("rev-parse", "HEAD~1").strip()
    assert excluded not in recorded and repo.head() in recorded
    item = reader.unit("2026-01-01T00:00:00+00:00", "2026-01-06T00:00:00+00:00")["pkg/six.py"]
    assert (item.tracking.c1_sha, item.tracking.censoring_reason) == (None, CUT_OFF)
    assert item.base_rate == BaseRate(0, 1, 0.0, END_OF_W)


# --- Checks 4, 5, 8 and 9 on hand-made records ---------------------------------------------

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)


def touch(path: str, new: str | None = None, deleted: bool = False) -> Entry:
    """Return a modification, rename or deletion of ``path``."""
    kind = ChangeType.DELETED if deleted else (ChangeType.RENAMED if new else ChangeType.MODIFIED)
    return Entry(FileChange(kind, FileSide(path, "o"),
                            None if deleted else FileSide(new or path, "n"), None, False,
                            (Hunk(1, 1, 1, 1),)))


def record(sha: str, days: float, status: TraceStatus | None, *changes: Entry) -> Record:
    """Return a commit ``days`` after the introduction."""
    return Record(sha, T0 + timedelta(days=days), provenance(status), changes)


INTRODUCTION = Introduction("c", False, T0, "pkg/m.py", "blob", ItemKey("s", "t", "f"), "W0000",
                            "warning", "message", 1, 0)


def test_check4_traversal_stops_at_the_first_commit_dated_after_the_end_of_w() -> None:
    """History order: a touch dated inside W after one dated past it is not counted.

    A date filter over (t, end of W] would count it (1).
    """
    history = [record("c", 0, NON), record("x", 200, NON, touch("pkg/m.py")),
               record("y", 100, NON, touch("pkg/m.py"))]
    rate = base_rate(INTRODUCTION, history, 6)
    assert (rate.ai_traced, rate.non_traced, rate.window_end) == (0, 0, T0.replace(month=7))


def test_check4_a_commit_dated_exactly_at_the_end_of_w_is_within_w() -> None:
    """"After" is strict, as for tracking."""
    history = [record("c", 0, NON), record("x", 181, NON, touch("pkg/m.py"))]
    assert T0 + timedelta(days=181) == T0.replace(month=7)
    assert base_rate(INTRODUCTION, history, 6).non_traced == 1


def test_check5_a_later_commit_dated_before_the_introduction_counts() -> None:
    """Clock skew: later in history but dated before t; a filter on (t, E] would drop it."""
    history = [record("c", 0, NON), record("s", -3, TraceStatus.AI_TRACED, touch("pkg/m.py"))]
    assert base_rate(INTRODUCTION, history, 6) == BaseRate(1, 0, 1.0, T0.replace(month=7))


def test_check8_unknown_provenance_of_a_touching_commit_fails_closed() -> None:
    """An unknown status is never read as non-traced (Task 6): the unit stops (P6)."""
    history = [record("c", 0, NON), record("u", 5, None, touch("pkg/m.py"))]
    with pytest.raises(ClassificationStopped) as stopped:
        base_rate(INTRODUCTION, history, 6)
    assert stopped.value.category == "unknown provenance" and "u" in stopped.value.cause


def test_check8_unknown_provenance_of_an_untouching_commit_does_not_matter() -> None:
    """Only a commit the base rate reads must be known."""
    history = [record("c", 0, NON), record("u", 5, None, touch("pkg/other.py"))]
    assert base_rate(INTRODUCTION, history, 6) == BaseRate(0, 0, None, T0.replace(month=7))


def test_check9_zero_denominator_is_null_not_zero() -> None:
    """No counted touch: share None, distinguished from a share of 0.0."""
    history = [record("c", 0, NON), record("b", 5, BOT, touch("pkg/m.py")),
               record("n", 6, NON, touch("pkg/m.py"))]
    assert base_rate(INTRODUCTION, history[:2], 6).ai_traced_share is None
    assert base_rate(INTRODUCTION, history, 6).ai_traced_share == 0.0


def test_two_changes_of_the_followed_path_fail_closed() -> None:
    """Git cannot list them, but a hand-made record can: inconsistent change (P6)."""
    history = [record("c", 0, NON), record("d", 5, NON, touch("pkg/m.py"),
                                      touch("pkg/m.py", "pkg/n.py"))]
    with pytest.raises(ClassificationStopped) as stopped:
        base_rate(INTRODUCTION, history, 6)
    assert stopped.value.category == "inconsistent file change"


def test_an_introduction_missing_from_the_history_is_an_error() -> None:
    """The introducing commit must be in the recorded history."""
    with pytest.raises(ValueError):
        base_rate(INTRODUCTION, [record("other", 0, NON)], 6)
