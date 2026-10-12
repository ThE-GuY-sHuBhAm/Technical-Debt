"""RE-08 (FR-53, FR-54): the introduction rule on FX-PAIRS and FX-RG1.

Acceptance checks use the real PackageFilter and the real (unclassified) trace status, so
each case shows exactly what can be decided today:

* PASS, decided without CL-03: an untouched line is never introduced (it is not new on
  an added line), and a commit outside its window introduces nothing (the detector asks
  nothing else).
* BLOCKED by CL-03 (strict expected failures): whether added and modified lines introduce
  needs the bot status and the package filter; so does the vendored warning of FX-RG1.
* NOT YET TESTABLE on FX-RG1 v1 (strict expected failures): RE-08 needs a tests-only
  commit, a bot commit and a merge commit that each add a warning. FX-RG1 v1's commits of
  those kinds add none, so the rule is not exercised by them; the warning cases arrive
  with FX-RG1 v2 (Day 6, Task 7 decision 5). FX-RG1 is unchanged here.

The SYNTHETIC — not CL-03 tests at the end check the detector's mechanics with a test
double that names package paths explicitly and with trace statuses passed in directly.
They are not CL-03 rules and are not evidence for RE-08 (Task 7 decision 4).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import make_fx_pairs
import make_fx_rg1
import pytest
from fx_rg1_clone import ClonedFxRg1

from tdea.analyzer.research.profile import (FAILED, OK, SYNTAX_ERROR, LintMessage,
                                            LintResult, ResearchProfile)
from tdea.analyzer.research.rg1.introduction import (CommitIntroductions, FileVersions,
                                                     IntroductionDetector)
from tdea.analyzer.research.rg1.package_filter import FilterRulesUnresolved, PackageFilter
from tdea.analyzer.research.rg1.provenance import MatchingRulesUnresolved, TraceStatus
from tdea.git_handler.commit_selector import CommitSelector, Window
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk
from tdea.git_handler.history_reader import HistoryReader
from tdea.git_handler.local_repository import LocalRepository
from tdea.git_handler.research_commit import RawCommitStrings, ResearchCommit

CL03 = ("BLOCKED: needs bot classification and the package filter, and CL-03 v0.1 defines "
        "no matching rule (Task 7: unresolved RE-08 cases stay blocked)")
CL03_REFUSALS = (MatchingRulesUnresolved, FilterRulesUnresolved)
E_PAIR = "e_added_modified_untouched"


class FixtureLacksCase(AssertionError):
    """FX-RG1 v1 has no commit of the kind an RE-08 case needs."""


@dataclass(frozen=True)
class CommitUnderTest:
    """One commit, its selection context and its file versions."""

    commit: ResearchCommit
    files: tuple[FileVersions, ...]


def versions_of(reader: HistoryReader, commit: ResearchCommit,
                profile: ResearchProfile) -> tuple[FileVersions, ...]:
    """Read and lint both versions of each changed .py file of ``commit``."""
    found = []
    for change in reader.file_changes(commit):
        if change.new is None or not change.new.path.endswith(".py"):
            found.append(FileVersions(change, None, None))
            continue
        before, before_content = None, b""
        if change.old is not None:
            before_content = reader.read_blob(commit.parents[0], change.old.path)
            before = profile.lint(before_content, Path(change.old.path).name)
        after_content = reader.read_blob(commit.sha, change.new.path)
        after = profile.lint(after_content, Path(change.new.path).name)
        found.append(FileVersions(change, before, after, before_content, after_content))
    return tuple(found)


@pytest.fixture(name="e_pair", scope="module")
def _e_pair(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CommitUnderTest]:
    """Return FX-PAIRS's RE-08 commit (added, modified and untouched flagged lines)."""
    root = tmp_path_factory.mktemp("fx-pairs-re08")
    pair = make_fx_pairs.build(root / "repo")[E_PAIR]
    (root / "clones").mkdir()
    with LocalRepository(root / "clones") as local:
        local.clone(root / "repo")
        reader = HistoryReader(local)
        history = reader.first_parent(pair.after_sha, datetime.now(timezone.utc))
        commit = [item for item in history.commits if item.sha == pair.after_sha][0]
        yield CommitUnderTest(commit, versions_of(reader, commit, ResearchProfile()))


@pytest.fixture(name="rg1_commits", scope="module")
def _rg1_commits(cloned_fx_rg1: ClonedFxRg1) -> dict[str, CommitUnderTest]:
    """Return FX-RG1's commits by label, with their file versions."""
    reader = HistoryReader(cloned_fx_rg1.local)
    profile = ResearchProfile()
    labels = {sha: label for label, sha in cloned_fx_rg1.fixture.shas.items()}
    return {labels[commit.sha]: CommitUnderTest(commit, versions_of(reader, commit, profile))
            for commit in cloned_fx_rg1.history.commits}


def real_detector() -> IntroductionDetector:
    """Return a detector with the real PackageFilter (CL-03 v0.1: it refuses .py paths)."""
    return IntroductionDetector(PackageFilter())


def lines_of(result: CommitIntroductions) -> list[int]:
    """Return the introduced lines."""
    return [introduction.line for introduction in result.introductions]


# --- PASS: decided without CL-03 ---------------------------------------------------------

def test_an_untouched_line_is_never_introduced(e_pair: CommitUnderTest) -> None:
    """The untouched flagged line 3 is not new on an added line; lines 4 and 5 are (FR-53).

    Conditions (1) and (2) alone rule line 3 out, whatever CL-03 decides; whether lines
    4 and 5 are introduced is the BLOCKED check below.
    """
    (versions,) = e_pair.files
    assert [o.line for o in real_detector().candidates(versions)] == [4, 5]


def test_a_commit_outside_its_window_introduces_nothing(e_pair: CommitUnderTest) -> None:
    """Not selected: nothing introduced, and neither CL-03 answer is asked for (FR-53)."""
    window = Window(datetime(2025, 1, 1, tzinfo=timezone.utc),
                    datetime(2025, 7, 1, tzinfo=timezone.utc))
    selected = e_pair.commit in CommitSelector([e_pair.commit]).select(window)
    assert not selected
    result = real_detector().detect(e_pair.commit, selected, None, e_pair.files)
    assert result == CommitIntroductions()


# --- BLOCKED by CL-03 --------------------------------------------------------------------

@pytest.mark.xfail(strict=True, raises=CL03_REFUSALS, reason=CL03)
def test_added_and_modified_lines_introduce(e_pair: CommitUnderTest) -> None:
    """Only the modified (4) and added (5) lines of a selected commit introduce."""
    result = real_detector().detect(e_pair.commit, True, None, e_pair.files)
    assert lines_of(result) == [4, 5]


@pytest.mark.xfail(strict=True, raises=CL03_REFUSALS, reason=CL03)
def test_a_warning_in_a_vendored_file_is_not_introduced(
    rg1_commits: dict[str, CommitUnderTest]
) -> None:
    """FX-RG1's vendored module adds W0611 on an added line; vendored code is not package."""
    target = rg1_commits["vendor_migrations"]
    assert any(real_detector().candidates(versions) for versions in target.files)
    result = real_detector().detect(target.commit, True, None, target.files)
    assert not result.introductions


# --- NOT YET TESTABLE on FX-RG1 v1 -------------------------------------------------------

def _require_a_warning(target: CommitUnderTest, what: str) -> None:
    """Raise FixtureLacksCase unless ``target`` adds a warning on an added line."""
    if not any(real_detector().candidates(versions) for versions in target.files):
        raise FixtureLacksCase(f"FX-RG1 v1's {what} adds no warning")


@pytest.mark.xfail(strict=True, raises=FixtureLacksCase,
                   reason="NOT YET TESTABLE: FX-RG1 v1's tests-only commit adds no warning; "
                          "FX-RG1 v2 is deferred to Day 6 (Task 7 decision 5)")
def test_a_tests_only_commit_introduces_nothing(rg1_commits: dict[str, CommitUnderTest]) -> None:
    """A commit touching only tests/ introduces nothing (package filter)."""
    target = rg1_commits["tests_only"]
    _require_a_warning(target, "tests-only commit")
    assert not real_detector().detect(target.commit, True, None, target.files).introductions


@pytest.mark.xfail(strict=True, raises=FixtureLacksCase,
                   reason="NOT YET TESTABLE: FX-RG1 v1's dependency-bot commit changes only "
                          "requirements.txt; FX-RG1 v2 is deferred to Day 6 (decision 5)")
def test_a_bot_commit_adding_a_warning_introduces_nothing(
    rg1_commits: dict[str, CommitUnderTest]
) -> None:
    """A bot commit that adds a warning introduces nothing (FR-54)."""
    target = rg1_commits["dependency_bot"]
    _require_a_warning(target, "dependency-bot commit")
    assert not real_detector().detect(target.commit, True, None, target.files).introductions


@pytest.mark.xfail(strict=True, raises=FixtureLacksCase,
                   reason="NOT YET TESTABLE: FX-RG1 v1's merge commit adds no warning; "
                          "FX-RG1 v2 is deferred to Day 6 (Task 7 decision 5)")
def test_the_merge_commits_introductions_carry_the_merge_flag(
    rg1_commits: dict[str, CommitUnderTest]
) -> None:
    """Introductions of the merge commit carry the merge flag (D-10)."""
    target = rg1_commits["merge"]
    assert target.commit.is_merge
    _require_a_warning(target, "merge commit")
    result = real_detector().detect(target.commit, True, None, target.files)
    assert result.introductions and all(i.is_merge for i in result.introductions)


def test_fx_rg1_v1_is_unchanged(cloned_fx_rg1: ClonedFxRg1) -> None:
    """The NOT YET TESTABLE cases above use FX-RG1 v1 as it is."""
    assert make_fx_rg1.VERSION == "1"
    assert cloned_fx_rg1.fixture.first_parent_shas()[-1] == cloned_fx_rg1.fixture.shas["latest"]


# --- SYNTHETIC — not CL-03: mechanics of the detector --------------------------------------

class SyntheticPackages:
    """SYNTHETIC — not CL-03: package paths named one by one by a test, and every query."""

    def __init__(self, *packages: str) -> None:
        """Treat exactly ``packages`` as package code."""
        self.packages = set(packages)
        self.asked: list[str] = []

    def is_package(self, path: str) -> bool:
        """Answer from the explicit set and record the question."""
        self.asked.append(path)
        return path in self.packages


def synthetic_detect(target: CommitUnderTest, packages: SyntheticPackages,
                     status: TraceStatus | None, selected: bool = True) -> CommitIntroductions:
    """Run the detector with a SYNTHETIC package double (Task 7 decision 4)."""
    return IntroductionDetector(packages).detect(target.commit, selected, status, target.files)


def test_synthetic_selected_non_bot_commit_introduces_its_new_added_lines(
    e_pair: CommitUnderTest
) -> None:
    """SYNTHETIC — not CL-03: with package code and non-traced status, lines 4 and 5 introduce."""
    result = synthetic_detect(e_pair, SyntheticPackages(f"pairs/{E_PAIR}.py"),
                              TraceStatus.NON_TRACED)
    assert lines_of(result) == [4, 5] and not result.failed_files
    footer = result.introductions[1]
    assert (footer.symbol, footer.normalised_text, footer.scope, footer.occurrence) == (
        "f-string-without-interpolation", 'print(f"footer")', "report", 0)
    assert footer.message_id == "W1309" and footer.category == "warning"
    assert (footer.commit_sha, footer.path, footer.is_merge) == (
        e_pair.commit.sha, f"pairs/{E_PAIR}.py", False)
    assert footer.blob == e_pair.files[0].change.new.blob
    assert footer.introduced_at == e_pair.commit.committed_at


@pytest.mark.parametrize("status", [TraceStatus.AI_TRACED, TraceStatus.NON_TRACED])
def test_synthetic_ai_traced_and_non_traced_commits_both_introduce(
    e_pair: CommitUnderTest, status: TraceStatus
) -> None:
    """SYNTHETIC — not CL-03: provenance does not change what is introduced, only bot does."""
    assert lines_of(synthetic_detect(e_pair, SyntheticPackages(f"pairs/{E_PAIR}.py"),
                                     status)) == [4, 5]


def test_synthetic_bot_commit_introduces_nothing(e_pair: CommitUnderTest) -> None:
    """SYNTHETIC — not CL-03: a bot commit introduces nothing (condition 5)."""
    result = synthetic_detect(e_pair, SyntheticPackages(f"pairs/{E_PAIR}.py"), TraceStatus.BOT)
    assert not result.introductions


def test_synthetic_unclassified_status_refuses_instead_of_guessing(
    e_pair: CommitUnderTest
) -> None:
    """SYNTHETIC — not CL-03: an unclassified commit with candidates is never read as non-bot."""
    with pytest.raises(MatchingRulesUnresolved, match="CL-03 v0.1"):
        synthetic_detect(e_pair, SyntheticPackages(f"pairs/{E_PAIR}.py"), None)


def test_synthetic_non_package_file_introduces_nothing(e_pair: CommitUnderTest) -> None:
    """SYNTHETIC — not CL-03: a file the double calls non-package introduces nothing."""
    packages = SyntheticPackages()
    assert synthetic_detect(e_pair, packages, TraceStatus.NON_TRACED) == CommitIntroductions()
    assert packages.asked == [f"pairs/{E_PAIR}.py"]


def test_synthetic_bot_commit_asks_no_package_question(e_pair: CommitUnderTest) -> None:
    """SYNTHETIC — not CL-03: once the commit is a bot commit, package status cannot matter."""
    packages = SyntheticPackages(f"pairs/{E_PAIR}.py")
    assert synthetic_detect(e_pair, packages, TraceStatus.BOT) == CommitIntroductions()
    assert not packages.asked


def test_synthetic_unclassified_status_refuses_before_any_package_question(
    e_pair: CommitUnderTest
) -> None:
    """SYNTHETIC — not CL-03: the trace status is settled first; the filter is not asked."""
    packages = SyntheticPackages(f"pairs/{E_PAIR}.py")
    with pytest.raises(MatchingRulesUnresolved):
        synthetic_detect(e_pair, packages, None)
    assert not packages.asked


def test_synthetic_nothing_is_asked_when_nothing_depends_on_it(e_pair: CommitUnderTest) -> None:
    """SYNTHETIC — not CL-03: unselected commits and commits without candidates ask nothing."""
    packages = SyntheticPackages(f"pairs/{E_PAIR}.py")
    assert synthetic_detect(e_pair, packages, None, selected=False) == CommitIntroductions()
    (versions,) = e_pair.files
    quiet = replace(versions, after=versions.before, after_content=versions.before_content,
                    change=replace(versions.change, hunks=()))
    assert synthetic_detect(CommitUnderTest(e_pair.commit, (quiet,)), packages, None) \
        == CommitIntroductions()
    assert not packages.asked


def _commit(parents: int = 1) -> ResearchCommit:
    """Return a SYNTHETIC commit record with ``parents`` parents."""
    moment = datetime(2026, 5, 1, 9, tzinfo=timezone.utc)
    return ResearchCommit("c" * 40, 3, tuple(str(n) * 40 for n in range(1, parents + 1)),
                          moment, moment + timedelta(minutes=5),
                          RawCommitStrings("A", "a@example.invalid", "A", "a@example.invalid",
                                           "Change"))


def _added_file(path: str, content: bytes, result: LintResult) -> FileVersions:
    """Return a SYNTHETIC added file with the given lint result."""
    lines = content.count(b"\n")
    change = FileChange(ChangeType.ADDED, None, FileSide(path, "d" * 40), None, False,
                        (Hunk(0, 0, 1, lines),))
    return FileVersions(change, None, result, b"", content)


W1309 = LintMessage("f-string-without-interpolation", "W1309", "warning", "Using an f-string",
                    2, "run")
SOURCE = b'def run():\n    print(f"x")\n'


def test_synthetic_added_file_introduces_and_merge_flag_is_carried() -> None:
    """SYNTHETIC — not CL-03: an added file's parent version is empty; a merge flag is carried."""
    added = _added_file("pkg/new.py", SOURCE, LintResult(OK, None, (W1309,)))
    result = IntroductionDetector(SyntheticPackages("pkg/new.py")).detect(
        _commit(parents=2), True, TraceStatus.NON_TRACED, (added,))
    assert lines_of(result) == [2] and result.introductions[0].is_merge


def test_synthetic_lint_failures_are_listed_without_a_package_question() -> None:
    """SYNTHETIC — not CL-03: a failed file introduces nothing and is listed, unclassified."""
    failed = LintResult(FAILED, SYNTAX_ERROR, ())
    files = (_added_file("pkg/broken.py", SOURCE, failed),
             _added_file("tests/broken.py", SOURCE, failed),
             _added_file("pkg/fine.py", SOURCE, LintResult(OK, None, (W1309,))))
    packages = SyntheticPackages("pkg/broken.py", "pkg/fine.py")
    result = IntroductionDetector(packages).detect(_commit(), True, TraceStatus.NON_TRACED, files)
    assert result.failed_files == ("pkg/broken.py", "tests/broken.py")
    assert [i.path for i in result.introductions] == ["pkg/fine.py"]
    assert packages.asked == ["pkg/fine.py"]


def test_synthetic_a_failing_parent_version_alone_introduces_nothing() -> None:
    """SYNTHETIC — not CL-03: FR-53 says either version; a broken parent blocks c too."""
    change = FileChange(ChangeType.MODIFIED, FileSide("pkg/m.py", "1" * 40),
                        FileSide("pkg/m.py", "2" * 40), None, False, (Hunk(2, 1, 2, 1),))
    versions = FileVersions(change, LintResult(FAILED, SYNTAX_ERROR, ()),
                            LintResult(OK, None, (W1309,)), b"def run(:\n", SOURCE)
    detector = IntroductionDetector(SyntheticPackages("pkg/m.py"))
    result = detector.detect(_commit(), True, TraceStatus.NON_TRACED, (versions,))
    assert result == CommitIntroductions((), ("pkg/m.py",))
    assert not detector.candidates(versions)


def test_synthetic_a_renamed_file_keeps_its_items_and_introduces_only_added_lines() -> None:
    """SYNTHETIC — not CL-03: a rename with edits matches across paths; only the new line counts."""
    before = b'def run():\n    print(f"x")\n'
    after = b'def run():\n    print(f"x")\n    print(f"y")\n'
    change = FileChange(ChangeType.RENAMED, FileSide("pkg/old.py", "1" * 40),
                        FileSide("pkg/new.py", "2" * 40), 90, False, (Hunk(2, 0, 3, 1),))
    y_line = replace(W1309, line=3)
    versions = FileVersions(change, LintResult(OK, None, (W1309,)),
                            LintResult(OK, None, (W1309, y_line)), before, after)
    result = IntroductionDetector(SyntheticPackages("pkg/new.py")).detect(
        _commit(), True, TraceStatus.NON_TRACED, (versions,))
    assert [(i.path, i.line, i.normalised_text) for i in result.introductions] == [
        ("pkg/new.py", 3, 'print(f"y")')]


def test_synthetic_a_binary_diff_is_listed_not_dropped() -> None:
    """SYNTHETIC — not CL-03: a .py file Git diffs as binary has no hunks; it is listed."""
    change = FileChange(ChangeType.ADDED, None, FileSide("pkg/blob.py", "3" * 40), None, True, ())
    versions = FileVersions(change, None, LintResult(OK, None, (W1309,)), b"", SOURCE)
    packages = SyntheticPackages("pkg/blob.py")
    result = IntroductionDetector(packages).detect(_commit(), True, None, (versions,))
    assert result == CommitIntroductions((), (), ("pkg/blob.py",)) and not packages.asked


def test_synthetic_a_lone_cr_file_uses_git_lines_against_the_hunks() -> None:
    """SYNTHETIC — not CL-03: Python sees 3 lines where Git sees 2; the added line is Git's 2.

    ``a = 1\\rb = f"x"`` is one Git line but two Python lines; Pylint's line 3 is Git's line 2,
    which the hunk adds. The record keeps Pylint's line number.
    """
    content = b'a = 1\rb = 2\nc = f"x"\n'
    message = replace(W1309, line=3, scope="")
    change = FileChange(ChangeType.MODIFIED, FileSide("pkg/cr.py", "4" * 40),
                        FileSide("pkg/cr.py", "5" * 40), None, False, (Hunk(1, 0, 2, 1),))
    versions = FileVersions(change, LintResult(OK, None, ()), LintResult(OK, None, (message,)),
                            b"a = 1\rb = 2\n", content)
    result = IntroductionDetector(SyntheticPackages("pkg/cr.py")).detect(
        _commit(), True, TraceStatus.NON_TRACED, (versions,))
    assert [(i.line, i.normalised_text) for i in result.introductions] == [(3, 'c = f"x"')]


@pytest.mark.parametrize("before, after", [(None, LintResult(OK, None, ())),
                                           (LintResult(OK, None, ()), None)])
def test_file_versions_refuse_inconsistent_inputs(before: LintResult | None,
                                                  after: LintResult | None) -> None:
    """A modified .py file needs both lint results; otherwise all warnings would look new."""
    change = FileChange(ChangeType.MODIFIED, FileSide("pkg/m.py", "1" * 40),
                        FileSide("pkg/m.py", "2" * 40), None, False, ())
    with pytest.raises(ValueError):
        FileVersions(change, before, after)


def test_synthetic_deleted_and_non_python_files_are_skipped() -> None:
    """SYNTHETIC — not CL-03: deletions and non-.py files introduce nothing and ask nothing."""
    deleted = FileVersions(FileChange(ChangeType.DELETED, FileSide("pkg/old.py", "e" * 40),
                                      None, None, False, (Hunk(1, 2, 0, 0),)),
                           LintResult(OK, None, (W1309,)), None, SOURCE, b"")
    readme = FileVersions(FileChange(ChangeType.MODIFIED, FileSide("README.md", "1" * 40),
                                     FileSide("README.md", "2" * 40), None, False,
                                     (Hunk(1, 1, 1, 1),)), None, None)
    packages = SyntheticPackages("pkg/old.py", "README.md")
    result = IntroductionDetector(packages).detect(_commit(), True, None, (deleted, readme))
    assert result == CommitIntroductions() and not packages.asked


def test_synthetic_a_new_occurrence_on_an_unchanged_line_is_not_introduced() -> None:
    """SYNTHETIC — not CL-03: renaming a function gives its body a new key, but no new line.

    The body line is unchanged, so its occurrence (now scoped ``after``) is new yet not on
    the added side of the hunks: condition (2) keeps it from being introduced.
    """
    before = b'def before():\n    print(f"x")\n'
    after = b'def after():\n    print(f"x")\n'
    change = FileChange(ChangeType.MODIFIED, FileSide("pkg/m.py", "1" * 40),
                        FileSide("pkg/m.py", "2" * 40), None, False, (Hunk(1, 1, 1, 1),))
    versions = FileVersions(change, LintResult(OK, None, (replace(W1309, scope="before"),)),
                            LintResult(OK, None, (replace(W1309, scope="after"),)),
                            before, after)
    detector = IntroductionDetector(SyntheticPackages("pkg/m.py"))
    assert not detector.candidates(versions)
    assert detector.detect(_commit(), True, TraceStatus.NON_TRACED, (versions,)) \
        == CommitIntroductions()
    