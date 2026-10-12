"""RE-08 (FR-53, FR-54) on FX-RG1 v2's three warning commits (Task 7 Day 6 decision 6).

FX-RG1 v1 has no tests-only, bot or merge commit that adds a warning, so test_re08's v1
cases stay NOT YET TESTABLE (unchanged). FX-RG1 v2 adds one of each, so the cases are now
testable, and BLOCKED by CL-03: with the real package filter and the real (unclassified)
trace status, the detector refuses rather than guess (strict expected failures).

The SYNTHETIC — not CL-03 checks show the detector's mechanics on the same commits, with
the package paths and the bot label given by the test; they are not RE-08 evidence and
do not resolve CL-03.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fx_rg1_clone import ClonedFxRg1V2
from run_support import FX_RG1_PACKAGES, SyntheticPackages

from tdea.analyzer.research.profile import ResearchProfile
from tdea.analyzer.research.rg1.introduction import (CommitIntroductions, FileVersions,
                                                     IntroductionDetector)
from tdea.analyzer.research.rg1.package_filter import FilterRulesUnresolved, PackageFilter
from tdea.analyzer.research.rg1.provenance import MatchingRulesUnresolved, TraceStatus
from tdea.git_handler.research_commit import ResearchCommit

CL03 = ("BLOCKED: whether this commit introduces needs bot classification and the package "
        "filter, and CL-03 v0.1 defines no matching rule (Task 7 decision 1)")
CL03_REFUSALS = (MatchingRulesUnresolved, FilterRulesUnresolved)
CASES = ("tests_only_warning", "bot_warning", "merge_warning")


def versions(cloned: ClonedFxRg1V2, commit: ResearchCommit) -> tuple[FileVersions, ...]:
    """Read and lint both versions of each changed .py file of ``commit``."""
    found = []
    profile = ResearchProfile()
    for change in cloned.reader.file_changes(commit):
        if change.new is None or not change.new.path.endswith(".py"):
            continue
        before, before_content = None, b""
        if change.old is not None:
            before_content = cloned.reader.read_blob(change.old.blob)
            before = profile.lint(before_content, Path(change.old.path).name)
        after_content = cloned.reader.read_blob(change.new.blob)
        after = profile.lint(after_content, Path(change.new.path).name)
        found.append(FileVersions(change, before, after, before_content, after_content))
    return tuple(found)


@pytest.mark.parametrize("label", CASES)
def test_each_v2_case_adds_a_warning_on_an_added_line(cloned_fx_rg1_v2: ClonedFxRg1V2,
                                                       label: str) -> None:
    """The cases now exist: each commit has a new warning on an added line (no CL-03 needed)."""
    detector = IntroductionDetector(PackageFilter())
    files = versions(cloned_fx_rg1_v2, cloned_fx_rg1_v2.commit(label))
    assert [len(detector.candidates(item)) for item in files] == [1]


def test_the_merge_case_is_a_merge_commit(cloned_fx_rg1_v2: ClonedFxRg1V2) -> None:
    """The merge commit has two parents, and its first-parent diff adds the flagged module."""
    merge = cloned_fx_rg1_v2.commit("merge_warning")
    assert merge.is_merge and len(merge.parents) == 2
    (added,) = versions(cloned_fx_rg1_v2, merge)
    assert added.path == "pkg/feature_flags.py"


@pytest.mark.parametrize("label", CASES)
@pytest.mark.xfail(strict=True, raises=CL03_REFUSALS, reason=CL03)
def test_re08_v2_case_with_the_real_rules(cloned_fx_rg1_v2: ClonedFxRg1V2, label: str) -> None:
    """RE-08 as written: tests-only and bot commits introduce nothing; merges carry the flag."""
    commit = cloned_fx_rg1_v2.commit(label)
    result = IntroductionDetector(PackageFilter()).detect(
        commit, True, None, versions(cloned_fx_rg1_v2, commit))
    if label == "merge_warning":
        assert result.introductions and all(item.is_merge for item in result.introductions)
    else:
        assert not result.introductions


def synthetic(cloned: ClonedFxRg1V2, label: str, status: TraceStatus) -> CommitIntroductions:
    """Detect with SYNTHETIC — not CL-03 package paths and a status given by the test."""
    commit = cloned.commit(label)
    return IntroductionDetector(SyntheticPackages(FX_RG1_PACKAGES)).detect(
        commit, True, status, versions(cloned, commit))


def test_synthetic_a_tests_only_commit_not_named_package_introduces_nothing(
    cloned_fx_rg1_v2: ClonedFxRg1V2
) -> None:
    """SYNTHETIC — not CL-03: tests/test_tracked.py is not in the double's list."""
    assert synthetic(cloned_fx_rg1_v2, "tests_only_warning", TraceStatus.NON_TRACED) \
        == CommitIntroductions()


def test_synthetic_a_commit_labelled_bot_introduces_nothing(
    cloned_fx_rg1_v2: ClonedFxRg1V2
) -> None:
    """SYNTHETIC — not CL-03: labelled a bot commit by the test, it introduces nothing."""
    assert synthetic(cloned_fx_rg1_v2, "bot_warning", TraceStatus.BOT) == CommitIntroductions()
    assert synthetic(cloned_fx_rg1_v2, "bot_warning", TraceStatus.NON_TRACED).introductions


def test_synthetic_the_merge_commits_introduction_carries_the_merge_flag(
    cloned_fx_rg1_v2: ClonedFxRg1V2
) -> None:
    """SYNTHETIC — not CL-03: D-10, the merge's first-parent diff introduces with the flag."""
    result = synthetic(cloned_fx_rg1_v2, "merge_warning", TraceStatus.NON_TRACED)
    assert [(item.path, item.line, item.is_merge) for item in result.introductions] == [
        ("pkg/feature_flags.py", 6, True)]
