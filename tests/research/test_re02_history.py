"""RE-02, history part (execution plan Day 2): steps 1, 2 and 4 on FX-RG1.

Step 1 runs the history reader on a fresh clone; the tdea research run command that will
drive it arrives on Day 6. Step 2 compares the commits with the frozen oracle,
git log --first-parent --format="%H %P %aI %cI". Step 3 (trailers hold identity keys)
needs FR-54 pseudonymisation and is marked expected-fail until Day 3. Step 4 reads the
clone time, HEAD and the selection. Step 5 (pinned HEAD and an earlier cut-off through
the run command) belongs to Day 6; the reader's cut-off rule is covered in
test_history_reader.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import make_fx_rg1
import pytest
from fixture_git import git_output
from fx_rg1_clone import CLONE_TIME, ClonedFxRg1

from tdea.git_handler.commit_selector import CommitSelector, Window


def oracle(cloned: ClonedFxRg1) -> list[list[str]]:
    """Return git log --first-parent --format="%H %P %aI %cI", root first."""
    lines = git_output(cloned.local.path, "log", "--first-parent",
                       "--format=%H %P %aI %cI").splitlines()
    return [line.split() for line in reversed(lines)]


def test_step2_same_shas_in_order_root_to_head(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Stored commits equal git log --first-parent from root to HEAD, in order."""
    shas = [commit.sha for commit in cloned_fx_rg1.history.commits]
    assert shas == [row[0] for row in oracle(cloned_fx_rg1)]
    assert shas == cloned_fx_rg1.fixture.first_parent_shas()
    assert [commit.position for commit in cloned_fx_rg1.history.commits] == list(range(15))
    assert cloned_fx_rg1.history.excluded_after_cutoff == 0


def test_step2_parents_and_merge_flag(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Parents equal git's; the merge commit has 2 parents and merge flag true."""
    for commit, row in zip(cloned_fx_rg1.history.commits, oracle(cloned_fx_rg1)):
        assert list(commit.parents) == row[1:-2]
    merges = [commit for commit in cloned_fx_rg1.history.commits if commit.is_merge]
    assert [commit.sha for commit in merges] == [cloned_fx_rg1.fixture.shas["merge"]]
    assert len(merges[0].parents) == 2
    side_branch = {cloned_fx_rg1.fixture.shas[label] for label in make_fx_rg1.SIDE_BRANCH}
    assert merges[0].parents[1] in side_branch


def test_step2_timestamps_are_utc(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Author and committer times are UTC and equal git's %aI and %cI instants."""
    for commit, row in zip(cloned_fx_rg1.history.commits, oracle(cloned_fx_rg1)):
        for stored, iso in ((commit.authored_at, row[-2]), (commit.committed_at, row[-1])):
            assert stored.utcoffset() == timedelta(0)
            assert stored == datetime.fromisoformat(iso).astimezone(timezone.utc)
    trailers = cloned_fx_rg1.commit("trailers")  # 03:00 at +05:30 is the previous day in UTC
    assert trailers.authored_at == datetime(2026, 2, 8, 21, 30, tzinfo=timezone.utc)


@pytest.mark.xfail(strict=True, reason="RE-02 step 3 needs FR-54 identity keys (Day 3)")
def test_step3_trailers_hold_keys(cloned_fx_rg1: ClonedFxRg1) -> None:
    """The trailer commit's stored message carries identity keys, not raw identities."""
    stored_message = cloned_fx_rg1.commit("trailers").raw.message  # Day 3: FR-54's rewrite
    assert "noreply@anthropic.com" not in stored_message
    assert make_fx_rg1.ASHA.email not in stored_message


def test_step4_clone_time_and_head_recorded(cloned_fx_rg1: ClonedFxRg1) -> None:
    """The clone time (UTC), HEAD at clone, default branch and address are recorded."""
    metadata = cloned_fx_rg1.metadata
    assert metadata.cloned_at == CLONE_TIME
    assert metadata.head_sha == cloned_fx_rg1.fixture.shas["latest"]
    assert metadata.default_branch == "main"
    assert metadata.url == str(cloned_fx_rg1.fixture.path)
    assert cloned_fx_rg1.history.cutoff == CLONE_TIME


def test_step4_only_window_commits_are_selected(cloned_fx_rg1: ClonedFxRg1) -> None:
    """A February 2026 window selects exactly the commits committed in it."""
    window = Window(datetime(2026, 2, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 1, tzinfo=timezone.utc))
    selected = CommitSelector(cloned_fx_rg1.history.commits).select(window)
    shas = cloned_fx_rg1.fixture.shas
    assert [commit.sha for commit in selected] == [
        shas[label] for label in ("generated", "trailers", "ai_committer", "ai_author")]


def test_step4_only_listed_shas_are_selected(cloned_fx_rg1: ClonedFxRg1) -> None:
    """A window with a SHA list selects only those commits, in history order."""
    shas = cloned_fx_rg1.fixture.shas
    window = Window(datetime(2026, 2, 1, tzinfo=timezone.utc),
                    datetime(2026, 3, 1, tzinfo=timezone.utc),
                    commits=(shas["ai_author"], shas["trailers"]))
    selected = CommitSelector(cloned_fx_rg1.history.commits).select(window)
    assert [commit.sha for commit in selected] == [shas["trailers"], shas["ai_author"]]
