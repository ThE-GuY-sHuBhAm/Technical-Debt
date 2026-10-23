"""RE-13 (FR-57): covariates of three FX-RG1 v4 items against hand calculations (Day 8).

SYNTHETIC — not CL-03. Every run here uses run_support's test doubles for provenance and
the package filter, because a real run is refused while CL-03 is incomplete; the origin
statuses are test labels. Passing tests show that the software computes, stores and links
FR-57's covariates as the provisional Day 8 readings say; they validate nothing about the
research methodology, the real classifier or real data.

RE-13: "Hand-compute with D-6 to D-9; compare | Every covariate matches; month, author_id,
repository and message filled; exploratory fields present and marked". Checked here:
every covariate value of the three items, month and author_id filled, repository and the
rewritten message reached through the item's unit and introducing commit (Day 8 decision
Q2), the exploratory fields present, reproducibility, and each rule's boundaries on
hand-made records. What stays BLOCKED (strict expected failures at the end): account
tenure (deferred pending the RM decision on Q9: the column is null, deliberately);
"marked", which needs FR-62's data dictionary (deferred; CL-14); and RE-13 with the real
rules (CL-03). RE-13 is therefore PARTIAL.
"""

from __future__ import annotations

import subprocess
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

import make_fx_rg1_v3 as v3
import make_fx_rg1_v4 as v4
import pytest
from handmade import Entry, Record, provenance
from make_fx_rg1 import ASHA, JANE, RAVI
from run_support import (FX_RG1_URL, PINNED_FACTS, LocalAccess, TickingClock, Workspace, rows,
                         run_file_text, synthetic_rules_v4)

from tdea.aggregator.key_store import KeyStore
from tdea.aggregator.research_export import RecordFile, data_dictionary, describe
from tdea.aggregator.research_models import Covariate
from tdea.aggregator.research_service import ResearchService
from tdea.analyzer.research.engine import ResearchEngine, Rg1Rules, RulesIncomplete
from tdea.analyzer.research.profile import FAILED, OK, LintMessage, LintResult
from tdea.analyzer.research.rg1.covariates import TENURE_DEFERRED, CovariateExtractor
from tdea.analyzer.research.rg1.fail_closed import ClassificationStopped
from tdea.analyzer.research.rg1.introduction import Introduction
from tdea.analyzer.research.rg1.matching import ItemKey
from tdea.analyzer.research.rg1.provenance import TraceStatus
from tdea.analyzer.research.rg1.pseudonymiser import IdentityKind, Pseudonymiser
from tdea.api.cli import EXIT_OK, main
from tdea.git_handler.file_changes import ChangeType, FileChange, FileSide, Hunk

UTC = timezone.utc
COLUMNS = ("file_origin", "volatility", "file_size", "file_age_days", "baseline_density",
           "commit_lines", "commit_files", "month", "author_id", "account_tenure_days",
           "prior_commits_to_file", "later_activity")
EXPLORATORY = ("account_tenure_days", "prior_commits_to_file", "later_activity")

# Hand calculations for the three FX-RG1 v4 items (make_fx_rg1_v4.STEPS), author_id aside.
# t is c's committer date; W = 6; cut-off 2028-06-01 00:00 UTC.
EXPECTED = {
    v4.ALPHA: {  # c = "Extend alpha", Jane, t = 2027-08-31 10:00
        # D-6: old_name.py -> alpha.py; added by "Add the old name module" (AI-labelled).
        "file_origin": "ai_traced",
        # D-7: [2027-02-28 10:00, 2027-08-31 10:00) (31 Feb clamped to 28): "Tune the old
        # name module" (exactly the start), the rename and the bot's "Bump alpha pins" = 3;
        # the add (09:00), "Touch alpha at t" (= t) and "Tidy alpha" (dated after t) are out.
        "volatility": 3 / 6,
        "file_size": 13,  # alpha(4, extra=True) has 13 lines
        "file_age_days": 184.0417,  # 2027-02-28 09:00 -> 2027-08-31 10:00 = 184 d 1 h
        # D-8: alpha(4) has 1 message (label's f-string) and 3 SLOC (VERSION, def, return;
        # the two docstrings are not SLOC in radon).
        "baseline_density": 1 / 3,
        "commit_lines": 7,  # alpha.py +5, notes.txt +1 -1, logo.bin binary: 0
        "commit_files": 3,
        "month": "2027-08",
        "account_tenure_days": None,  # TENURE_DEFERRED
        "prior_commits_to_file": 2,  # Jane's add and Jane's rename
        # E = 2028-02-29 10:00 (31 Feb clamped): "Note the alpha change", "Backdated note"
        # (dated before t, counted) and "Leap-day note" (exactly E); "March note" ends the
        # walk, so "Backdated February note" is not reached.
        "later_activity": 3,
    },
    v4.BETA: {  # c = "Extend beta", Ravi, t = 2027-12-01 00:00
        "file_origin": "bot",  # added by "Add generated beta" (bot-labelled)
        # [2027-06-01 00:00, t): "Tune beta" (exactly the start) and "Adjust beta" = 2.
        "volatility": 2 / 6,
        "file_size": 11,
        "file_age_days": 260.5,  # 2027-03-15 12:00 -> 2027-12-01 00:00
        "baseline_density": 0.0,  # beta(2): 0 messages, 2 SLOC
        "commit_lines": 5,
        "commit_files": 1,
        "month": "2027-12",
        "account_tenure_days": None,
        "prior_commits_to_file": 1,  # "Tune beta"
        # E = 2028-06-01 00:00 = the cut-off, so recorded: "March note", "Cut-off note"
        # (exactly E) and "Backdated May note"; "After the cut-off" is not recorded, so it
        # neither counts nor ends the walk (recorded history only, Day 8 decision B).
        "later_activity": 3,
    },
    v4.GAMMA: {  # c = "Add gamma", Asha, t = 2028-01-10 08:00
        "file_origin": "non_traced",  # c adds the file again: the draft's lineage ended
        "volatility": 0.0,  # the draft's add and delete belong to the earlier lineage
        "file_size": 6,
        "file_age_days": 0.0,
        "baseline_density": None,  # a new file
        "commit_lines": 6,
        "commit_files": 1,
        "month": "2028-01",
        "account_tenure_days": None,
        "prior_commits_to_file": 0,
        "later_activity": None,  # E = 2028-07-10 08:00 is after the cut-off
    },
}
AUTHORS = {v4.ALPHA: JANE, v4.BETA: RAVI, v4.GAMMA: ASHA}
MESSAGES = {  # the rewritten messages as stored: no identity to rewrite, Git's final LF
    v4.ALPHA: "Extend alpha\n", v4.BETA: "Extend beta\n", v4.GAMMA: "Add gamma\n"}
COVARIATES = ("SELECT d.path, " + ", ".join(f"v.{name}" for name in COLUMNS)
              + " FROM covariate v JOIN debt_item d USING (item_id) ORDER BY d.path")


def v4_service(workspace: Workspace, rules: Rg1Rules | None = None) -> ResearchService:
    """Return a service whose clone and run clocks stand at FX-RG1 v4's clone time."""
    engine = ResearchEngine(rules if rules is not None else synthetic_rules_v4(), None,
                            workspace.access, clock=lambda: v4.CLONE_TIME)
    return ResearchService(engine, TickingClock(v4.CLONE_TIME), paths=workspace.paths(),
                           environment=PINNED_FACTS)


def v4_run(root: Path, fixture: v4.FxRg1V4, key: bytes | None = None) -> Workspace:
    """Run the RE-13 window of FX-RG1 v4 into a fresh store (SYNTHETIC — not CL-03)."""
    root.mkdir(parents=True)
    workspace = Workspace(root, LocalAccess({"tdea-fixtures/fx-rg1": fixture.path}))
    if key is not None:
        target = workspace.paths().key_file
        target.parent.mkdir(mode=0o700)
        target.write_bytes(key)
        target.chmod(0o600)
    text = run_file_text([(FX_RG1_URL, (v4.COVARIATES_WINDOW,), None)], w_months=v4.W_MONTHS,
                         cutoff="2028-06-01T00:00:00Z")
    assert main(["research", "run", str(workspace.run_file(text))],
                service=v4_service(workspace)) == EXIT_OK
    return workspace


@pytest.fixture(name="re13", scope="module")
def _re13(fx_rg1_v4: v4.FxRg1V4, tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    """Run the RE-13 window once for this module."""
    return v4_run(tmp_path_factory.mktemp("re13") / "ws", fx_rg1_v4)


def stored(workspace: Workspace) -> dict[str, dict[str, object]]:
    """Return each item's covariate row, keyed by the item's path."""
    return {row[0]: dict(zip(COLUMNS, row[1:])) for row in rows(workspace.store, COVARIATES)}


def test_fx_rg1_v4_keeps_every_v1_to_v3_commit(fx_rg1_v4: v4.FxRg1V4,
                                               fx_rg1_v3: v3.FxRg1V3) -> None:
    """v4 is appended: every labelled v1, v2 and v3 SHA is unchanged and v3's head is v4's base."""
    assert fx_rg1_v4.v3_shas == fx_rg1_v3.shas
    assert {label: fx_rg1_v4.shas[label] for label in fx_rg1_v3.shas} == fx_rg1_v3.shas
    chain = subprocess.run(["git", "-C", str(fx_rg1_v4.path), "rev-list", "--first-parent",
                            "--reverse", "HEAD"], capture_output=True, text=True,
                           check=True).stdout.split()
    appended = len(v4.FIRST_PARENT_V4)
    assert chain[-appended:] == [fx_rg1_v4.shas[label] for label in v4.FIRST_PARENT_V4]
    assert chain[-appended - 1] == fx_rg1_v3.shas["module_fix"]  # v3's last commit


def test_the_window_introduces_exactly_the_three_re13_items(re13: Workspace) -> None:
    """One item per module, each introduced by its c."""
    assert rows(re13.store, "SELECT d.path, c.message FROM debt_item d JOIN 'commit' c "
                            "ON c.commit_id = d.introducing_commit_id ORDER BY d.path") == [
        (path, MESSAGES[path]) for path in sorted(EXPECTED)]


@pytest.mark.parametrize("path", sorted(EXPECTED))
def test_every_covariate_equals_the_hand_calculation(path: str, re13: Workspace) -> None:
    """RE-13: each stored covariate equals the hand calculation (author_id: next test)."""
    found = stored(re13)[path]
    assert {name: found[name] for name in EXPECTED[path]} == EXPECTED[path]


@pytest.mark.parametrize("path", sorted(EXPECTED))
def test_author_id_is_the_author_e_mail_key_never_the_e_mail(path: str,
                                                             re13: Workspace) -> None:
    """FR-57: "Author" is the author e-mail key of c, under the run's key file (NFR-27)."""
    key = KeyStore(re13.paths().key_file).key()
    expected = Pseudonymiser(key).identity_key(AUTHORS[path].email, IdentityKind.EMAIL)
    assert stored(re13)[path]["author_id"] == expected
    assert rows(re13.store, "SELECT c.author_email_key FROM debt_item d JOIN 'commit' c "
                            "ON c.commit_id = d.introducing_commit_id WHERE d.path = ?",
                path) == [(expected,)]
    assert AUTHORS[path].email not in str(stored(re13)[path])


def test_month_author_repository_and_message_are_filled(re13: Workspace) -> None:
    """RE-13: month and author_id on the row; repository and message through its links (Q2)."""
    linked = rows(re13.store,
                  "SELECT d.path, v.month, v.author_id, r.url, c.message FROM covariate v "
                  "JOIN debt_item d USING (item_id) JOIN unit u USING (unit_id) "
                  "JOIN repository r ON r.repository_id = u.repository_id "
                  "JOIN 'commit' c ON c.commit_id = d.introducing_commit_id ORDER BY d.path")
    assert [(path, month, url, message) for path, month, _key, url, message in linked] == [
        (path, EXPECTED[path]["month"], FX_RG1_URL, MESSAGES[path]) for path in sorted(EXPECTED)]
    assert all(key and len(key) == 64 for _path, _month, key, _url, _message in linked)


def test_exploratory_fields_are_present_and_tenure_is_deferred(re13: Workspace) -> None:
    """The three exploratory columns exist and are filled as computed; tenure is null on
    every row while author_id is not: deferred pending the RM decision on Q9 (R2.1)."""
    columns = [row[1] for row in rows(re13.store, "PRAGMA table_info(covariate)")]
    assert set(EXPLORATORY) <= set(columns)
    values = stored(re13)
    assert [(values[path]["prior_commits_to_file"], values[path]["later_activity"])
            for path in sorted(values)] == [(2, 3), (1, 3), (0, None)]
    assert all(row["account_tenure_days"] is None and row["author_id"] is not None
               for row in values.values())
    assert TENURE_DEFERRED == "deferred pending RM decision (Q9)"


def test_every_debt_item_has_exactly_one_covariate_row(re13: Workspace) -> None:
    """The row's key is the item: one row per debt item, none without one."""
    assert rows(re13.store, "SELECT count(*), count(DISTINCT item_id) FROM covariate") == [
        (3, 3)]
    assert rows(re13.store, "SELECT count(*) FROM debt_item d LEFT JOIN covariate v "
                            "USING (item_id) WHERE v.item_id IS NULL") == [(0,)]


def test_covariates_are_reproducible(re13: Workspace, fx_rg1_v4: v4.FxRg1V4,
                                     tmp_path: Path) -> None:
    """A second run into a fresh store with the same key file stores identical covariates."""
    again = v4_run(tmp_path / "again", fx_rg1_v4, re13.paths().key_file.read_bytes())
    assert rows(again.store, COVARIATES) == rows(re13.store, COVARIATES)


# --- the rules on hand-made records (Record says which steps Git cannot produce) -------

T = datetime(2027, 8, 31, 10, 0, tzinfo=UTC)  # t; t - 6 months = 2027-02-28 10:00
START = datetime(2027, 2, 28, 10, 0, tzinfo=UTC)
CUTOFF = datetime(2028, 6, 1, tzinfo=UTC)
PATH = "pkg/m.py"
KEY = "a" * 64
OTHER = "b" * 64
TICK = timedelta(microseconds=1)


def side(path: str, blob: str) -> FileSide:
    """Return one side of a change."""
    return FileSide(path, blob)


def added(path: str = PATH, blob: str = "b0") -> FileChange:
    """Return an add of ``path``."""
    return FileChange(ChangeType.ADDED, None, side(path, blob), None, False, (Hunk(0, 0, 1, 2),))


def modified(path: str = PATH, old: str = "b0", new: str = "b1") -> FileChange:
    """Return a modification of ``path`` with one changed line."""
    return FileChange(ChangeType.MODIFIED, side(path, old), side(path, new), None, False,
                      (Hunk(1, 1, 1, 1),))


def renamed(old_path: str, new_path: str, blob: str = "b0") -> FileChange:
    """Return a pure rename."""
    return FileChange(ChangeType.RENAMED, side(old_path, blob), side(new_path, blob), 100,
                      False, ())


def deleted(path: str = PATH, blob: str = "b0") -> FileChange:
    """Return a deletion of ``path``."""
    return FileChange(ChangeType.DELETED, side(path, blob), None, None, False,
                      (Hunk(1, 2, 0, 0),))


def commit(sha: str, when: datetime, *changes: FileChange, key: str | None = KEY,
           status: TraceStatus | None = TraceStatus.NON_TRACED) -> Record:
    """Return a hand-made recorded commit."""
    return Record(sha, when, provenance(status), tuple(Entry(change) for change in changes),
                  author_email_key=key)


def introduction(sha: str = "c", when: datetime = T, path: str = PATH) -> Introduction:
    """Return a hand-made introduction at ``sha``."""
    return Introduction(sha, False, when, path, "b9", ItemKey("s", "t", "f"), "W1309",
                        "warning", "m", 1, 1)


class Source:
    """Hand-made blobs and lint results; every blob is three statements, one message each."""

    def __init__(self, status: str = OK, content: bytes = b"A = 1\nB = 2\nC = 3\n") -> None:
        """Answer every read with ``content`` and every lint with ``status``."""
        self.status = status
        self.content = content

    def read_blob(self, blob: str) -> bytes:
        """Return the same content for every blob."""
        return self.content

    def lint(self, content: bytes, path: str, blob: str) -> LintResult:
        """Return one message, or a failure."""
        message = LintMessage("s", "W0000", "warning", "m", 1, "")
        return LintResult(self.status, None, (message,) if self.status == OK else ())


def extract(history: list[Record], source: Source | None = None, *,
            cutoff: datetime = CUTOFF, item: Introduction | None = None,
            radon: object | None = None):
    """Return the covariates of ``item`` (default: c's introduction) over ``history``."""
    extractor = CovariateExtractor(history, source or Source(), cutoff, 6, radon)
    return extractor.extract(item or introduction())


def test_volatility_counts_t_minus_6_months_and_excludes_t_and_later_dates() -> None:
    """[t - 6 months, t): the start counts, a microsecond before it and t itself do not;
    a commit placed before c but dated after t (clock skew) does not count."""
    history = [commit("add", START - TICK, added()),
               commit("start", START, modified()),
               commit("last", T - TICK, modified()),
               commit("skew", T + timedelta(days=1), modified()),
               commit("at_t", T, modified()),
               commit("c", T, modified())]
    found = extract(history)
    assert found.volatility == 2 / 6
    assert found.prior_commits_to_file == 5  # every lineage commit before c, same key


def test_a_renamed_file_is_followed_back_to_its_add() -> None:
    """D-6, D-7, Q10: the old name's commits belong to the lineage; another file's do not."""
    history = [commit("add", START, added("pkg/old.py"), status=TraceStatus.AI_TRACED),
               commit("other", START, added("pkg/other.py")),
               commit("move", START, renamed("pkg/old.py", PATH), key=OTHER),
               commit("c", T, modified())]
    found = extract(history)
    assert (found.file_origin, found.volatility, found.prior_commits_to_file) == (
        "ai_traced", 2 / 6, 1)


def test_file_age_is_kept_negative_under_clock_skew() -> None:
    """The adding commit dated after t: the age is negative, never clamped (Q6)."""
    history = [commit("add", T + timedelta(hours=6), added()), commit("c", T, modified())]
    assert extract(history).file_age_days == -0.25


def test_file_age_and_density_when_c_adds_the_file() -> None:
    """c adds the file: origin is c's status, age 0.0, density null, no lineage before c."""
    history = [commit("c", T, added(), status=TraceStatus.BOT)]
    found = extract(history)
    assert (found.file_origin, found.file_age_days, found.baseline_density,
            found.volatility, found.prior_commits_to_file) == ("bot", 0.0, None, 0.0, 0)


def test_density_is_messages_per_sloc_and_0_for_zero_sloc() -> None:
    """D-8: one message over three SLOC; a comment-only parent has SLOC 0 and density 0.0."""
    history = [commit("add", START, added()), commit("c", T, modified())]
    assert extract(history).baseline_density == 1 / 3
    assert extract(history, Source(content=b"# only a comment\n")).baseline_density == 0.0


class FailingRadon:
    """Radon that cannot read the version (Git cannot produce this after a good lint)."""

    def __init__(self, error: Exception) -> None:
        """Fail with ``error``."""
        self.error = error

    def sloc(self, content: bytes) -> int:
        """Raise the configured error."""
        raise self.error


@pytest.mark.parametrize("error", [SyntaxError("x"), tokenize.TokenError("x"),
                                   UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")])
def test_a_parent_radon_cannot_read_fails_closed(error: Exception) -> None:
    """Q12: radon failing on an existing parent version stops the unit, never null."""
    history = [commit("add", START, added()), commit("c", T, modified())]
    with pytest.raises(ClassificationStopped) as stopped:
        extract(history, radon=FailingRadon(error))
    assert stopped.value.category == "baseline density not computable"
    assert "of pkg/m.py at commit c cannot" in stopped.value.cause


def test_a_parent_without_a_successful_lint_fails_closed() -> None:
    """Hand-made: FR-53 never introduces an item over a failed parent lint; if it did, stop."""
    history = [commit("add", START, added()), commit("c", T, modified())]
    with pytest.raises(ClassificationStopped, match="no successful research-profile lint"):
        extract(history, Source(status=FAILED))


@pytest.mark.parametrize("history", [
    [commit("edit", START, modified()), commit("c", T, modified())],  # no add recorded
    [commit("add", START, added()), commit("gone", START, deleted()),
     commit("c", T, modified())],  # deleted before an add is found
    [commit("add", START, added()), commit("away", START, renamed(PATH, "pkg/x.py")),
     commit("c", T, modified())],  # renamed away before an add is found
    [commit("add", START, added()), commit("c", T, modified("pkg/elsewhere.py"))],  # c lacks it
], ids=["no-add", "deleted-before-add", "renamed-away-before-add", "c-without-the-file"])
def test_an_origin_the_recorded_history_does_not_establish_fails_closed(
        history: list[Record]) -> None:
    """Q4 and decision B: no origin is inferred from outside the recorded lineage; the
    message names the item's path at c and c's SHA."""
    with pytest.raises(ClassificationStopped) as stopped:
        extract(history)
    assert stopped.value.category == "file origin not established"
    assert stopped.value.cause.startswith("Following pkg/m.py back from commit c ")


def test_an_unestablished_origin_names_the_path_at_c_even_after_a_rename() -> None:
    """The stop names the item's file as it stands at c, not an older name it was followed to."""
    history = [commit("edit", START, modified("pkg/old.py")),
               commit("move", START, renamed("pkg/old.py", PATH)), commit("c", T, modified())]
    with pytest.raises(ClassificationStopped) as stopped:
        extract(history)
    assert stopped.value.cause.startswith("Following pkg/m.py back from commit c ")


def test_two_changes_of_the_followed_path_in_one_commit_fail_closed() -> None:
    """Hand-made (Git lists one change per path): the lineage cannot be followed."""
    history = [commit("add", START, added()),
               commit("twice", START, modified(), modified(old="b1", new="b2")),
               commit("c", T, modified())]
    with pytest.raises(ClassificationStopped) as stopped:
        extract(history)
    assert stopped.value.category == "inconsistent file change"


def test_an_unknown_origin_status_fails_closed() -> None:
    """Hand-made (the unit-start guard stops it first in a run): never read as non-traced."""
    history = [commit("add", START, added(), status=None), commit("c", T, modified())]
    with pytest.raises(ClassificationStopped, match="no known trace status"):
        extract(history)


def test_later_activity_counts_to_the_end_of_w_and_stops_at_the_first_later_date() -> None:
    """Q11: after c, the author's commits up to E = t + 6 months (E itself counts, a commit
    dated before t counts); the first commit dated after E ends the walk; other authors'
    commits do not count."""
    end = datetime(2028, 2, 29, 10, 0, tzinfo=UTC)  # 31 Feb 2028 clamped
    history = [commit("add", START, added()), commit("c", T, modified()),
               commit("skew", T - timedelta(days=1), modified("pkg/x.py")),
               commit("other", T + timedelta(days=1), key=OTHER),
               commit("at_e", end), commit("past", end + TICK, key=OTHER),
               commit("after_stop", T + timedelta(days=2))]
    assert extract(history).later_activity == 2


@pytest.mark.parametrize(("cutoff", "expected"), [
    (datetime(2028, 2, 29, 10, 0, tzinfo=UTC), 0),  # E = the cut-off: fully recorded
    (datetime(2028, 2, 29, 10, 0, tzinfo=UTC) - TICK, None),  # E after the cut-off
])
def test_later_activity_is_null_when_w_ends_after_the_cut_off(cutoff: datetime,
                                                              expected: int | None) -> None:
    """Q11 option (a): null, not a partial count, when t + W passes the cut-off."""
    history = [commit("add", START, added()), commit("c", T, modified())]
    assert extract(history, cutoff=cutoff).later_activity == expected


def test_without_an_author_key_author_id_and_the_exploratory_fields_are_null() -> None:
    """Q8: no author e-mail key means no author_id and null prior and later activity."""
    history = [commit("add", START, added(), key=None), commit("c", T, modified(), key=None)]
    found = extract(history)
    assert (found.author_id, found.account_tenure_days, found.prior_commits_to_file,
            found.later_activity) == (None, None, None, None)
    assert found.volatility == 1 / 6  # the other covariates are still computed


def test_the_month_is_taken_in_utc() -> None:
    """Q7: 2027-08-01 02:00 at UTC+05:30 is still July in UTC."""
    india = timezone(timedelta(hours=5, minutes=30))
    moment = datetime(2027, 8, 1, 2, 0, tzinfo=india)
    history = [commit("c", moment, added())]
    assert extract(history, item=introduction(when=moment)).month == "2027-07"


@pytest.mark.parametrize(("item", "w_months"), [
    (introduction("missing"), 6),  # c is not in the recorded history
    (introduction(when=datetime(2027, 8, 31, 10, 0)), 6),  # a date without its time zone
    (introduction(), 0),  # W below one month
])
def test_impossible_inputs_are_refused(item: Introduction, w_months: int) -> None:
    """Programming errors, not data: they raise instead of producing a value."""
    with pytest.raises(ValueError):
        CovariateExtractor([commit("c", T, added())], Source(), CUTOFF,
                           w_months).extract(item)


# --- what stays BLOCKED -----------------------------------------------------------------

@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="BLOCKED: Q9. Account tenure is deferred pending the RM decision on "
                          "the scope and meaning of 'first recorded commit' (R2.1)")
def test_account_tenure_is_computed(re13: Workspace) -> None:
    """FR-57: account tenure (days since the author's first recorded commit)."""
    assert all(row["account_tenure_days"] is not None for row in stored(re13).values())


def test_exploratory_fields_are_marked_in_the_data_dictionary() -> None:
    """RE-13: "exploratory fields present and marked" (FR-57 outputs; FR-62, sprint 23 Oct).

    data_dictionary.csv flags exactly the three exploratory covariate columns; the
    export test (test_re19_export) shows the flag in a written file.
    """
    names = list(Covariate.__table__.columns.keys())
    assert set(EXPLORATORY) <= set(names)
    record = RecordFile("covariate", tuple(describe("covariate", name, str) for name in names))
    assert {row[1] for row in data_dictionary([record]) if row[6]} == set(EXPLORATORY)


@pytest.mark.xfail(strict=True, raises=RulesIncomplete,
                   reason="BLOCKED: CL-03. A run of FX-RG1 with the real rules is refused "
                          "before it starts, so RE-13 cannot be run as written")
def test_re13_as_written_with_the_real_rules(fx_rg1_v4: v4.FxRg1V4) -> None:
    """RE-13 on FX-RG1 with FR-54's classifier, FR-51's package filter and D-4's lists."""
    engine = ResearchEngine(Rg1Rules(), None,
                            LocalAccess({"tdea-fixtures/fx-rg1": fx_rg1_v4.path}),
                            clock=lambda: v4.CLONE_TIME)
    engine.check_rules()  # the CL-03 gate every real run passes before anything else
