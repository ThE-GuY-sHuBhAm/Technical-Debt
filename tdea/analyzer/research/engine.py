"""ResearchEngine: the analyzer's facade for the research engine (Freeze section 2).

The aggregator reaches everything below it through this facade, so it never imports the
git_handler (NFR-14). It offers:

* check_url(): FR-02 on a run file's repository URL, with no connection (Day 5).
* check_environment() and run_metadata(): the NFR-24 check and what FR-52 records about
  the profile, the tools, the diff settings and the rule lists.
* check_rules(): the CL-03 refusal (Task 7 Day 6 decision 1). A real run needs commits
  classified (FR-54), package files filtered (FR-51 d) and, from Day 7, bulk changes
  recognised (FR-56 a1, a3, the D-4 lists; Day 7 decision); CL-03 v0.1 marks the AI-tool,
  bot, package-filter and both bulk lists incomplete and defines no matching rule, so a
  run is refused with exit 3 before any lock, clone or write. The refusal reads only the
  lists' completeness; nothing about CL-03 is decided here.
* open_repository() and run_rg1_unit(): one RG1 repository and one unit (unit_run),
  through introductions, tracking, disappearance modes and base rates.

Failures carry the three FR-05 parts and, where an agreed exit code exists, that code:
RulesIncomplete and HistoryChanged 3, SelectionInvalid 1. A UnitStopped for a clone or
network failure has exit 4 (FR-48, Freeze revision 116: "incomplete: a unit stopped on a
clone, network or disk error; running again resumes"; Stage 3, E-2). Any other
UnitStopped has none; it names its failure category and the open item that blocks the
code: CL-19 for a failed accessibility check at run time, and none for failures no source
covers (history read failure, tracking stopped on a binary diff). The categorisation is
Day 6's reading. From
Day 7 the fail-closed stops of FR-56 (fail_closed.py: CL-17's module-level case, an
unlocatable scope, unknown provenance, an unreadable version, an inconsistent file change)
are UnitStopped too, with their category and no open item for the code: they use Day 6's
unresolved-code path and no exit code is mapped for them (Day 7 decision P6).

The rules, the profile, the repository access (FR-03 check and clone source) and the
clock can be replaced, so tests can run units on local fixtures with test doubles; the
defaults are the real components. The clone folder is given per repository by the caller,
which owns it; discard_stale_clones() removes clones a killed run left there (FR-10).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tdea.analyzer.research.profile import (EnvironmentFacts, LintCachePort,
                                            ResearchProfile)
from tdea.analyzer.research.rg1.bulk import BulkRules, BulkRulesUnresolved, Cl03BulkRules
from tdea.analyzer.research.rg1.fail_closed import ClassificationStopped
from tdea.analyzer.research.rg1.package_filter import FilterRulesUnresolved, PackageFilter
from tdea.analyzer.research.rg1.provenance import (ListVersion, MatchingRulesUnresolved,
                                                   ProvenanceClassifier)
from tdea.analyzer.research.rg1.pseudonymiser import Pseudonymiser
from tdea.analyzer.research.rg1.tracking import TrackingStopped
from tdea.analyzer.research.rg1.unit_run import (Classifier, HistoryChanged, HistorySource,
                                                 PackageRules, RecordedRepository,
                                                 RepositoryAccess, RepositoryPlan,
                                                 RepositoryRecord,
                                                 RepositorySession, SelectionInvalid,
                                                 SessionParts, UnitOutcome, UnitWindow,
                                                 commit_record)
from tdea.analyzer.research.rg10.counts import SmellCounter
from tdea.analyzer.research.rg10.prepare import PREPARE_MODULE_VERSION
from tdea.git_handler.errors import ErrorCode, TdeaError
from tdea.git_handler.github_client import GitHubClient
from tdea.git_handler.history_reader import (RENAME_THRESHOLD, HistoryReader,
                                             research_diff_settings)
from tdea.git_handler.local_repository import LocalRepository, remove_stale_clones
from tdea.git_handler.url_validator import RepositoryRef, UrlValidator

CL03_UNRESOLVED = (MatchingRulesUnresolved, FilterRulesUnresolved, BulkRulesUnresolved)
__all__ = ["CheckedUrl", "GitHubAccess", "HistoryChanged", "RecordedRepository",
           "RepositoryPlan",
           "ResearchEngine", "Rg1Rules", "RulesIncomplete", "SelectionInvalid",
           "UnitOutcome", "UnitStopped", "UnitWindow", "UrlRejected"]


@dataclass(frozen=True)
class CheckedUrl:
    """A repository URL that passed FR-02, with the repository it names."""

    url: str
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Return owner/name."""
        return f"{self.owner}/{self.name}"


class UrlRejected(Exception):
    """A repository URL failed FR-02; carries the three FR-05 parts."""

    def __init__(self, error_type: str, cause: str, action: str) -> None:
        """Keep the three parts of the git_handler's error, never raw exception text."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action


class RulesIncomplete(Exception):
    """CL-03 is incomplete, so no research run may start (Day 6 decision 1, exit 3)."""

    exit_code = 3

    def __init__(self, incomplete: tuple[str, ...], detail: str | None = None) -> None:
        """Name the incomplete lists, or the refusal a component raised."""
        self.error_type = "Research run refused"
        named = (f"lists {', '.join(incomplete[:-1])} and {incomplete[-1]} are"
                 if len(incomplete) > 1 else f"list {''.join(incomplete)} is")
        self.cause = (detail if detail is not None else
                      f"The CL-03 rule {named} marked incomplete, so commits cannot be "
                      "classified (FR-54), package files filtered (FR-51 d) or bulk "
                      "changes recognised (FR-56 a1, a3) with authoritative rules.")
        self.action = ("Run research only with a TDEA version that holds the finalised "
                       "CL-03 lists and matching rules; validate works meanwhile.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


EXIT_INCOMPLETE = 4  # FR-48: a unit stopped on a clone, network or disk error; rerun resumes
CL19 = "CL-19"  # exit code for a failed accessibility check at run time: not defined
ACCESSIBILITY = "failed accessibility check"
_INCOMPLETE_CATEGORIES = {  # failure category of the stops that end with exit 4
    ErrorCode.CLONE_FAILED: "clone failure",
    ErrorCode.NETWORK: "network failure",
}
_STOP_CATEGORIES = {  # failure category and the open item that blocks its exit code
    ErrorCode.NOT_FOUND: (ACCESSIBILITY, CL19),
    ErrorCode.NOT_ACCESSIBLE: (ACCESSIBILITY, CL19),
    ErrorCode.PRIVATE_REPOSITORY: (ACCESSIBILITY, CL19),
    ErrorCode.EMPTY_REPOSITORY: (ACCESSIBILITY, CL19),
    ErrorCode.AUTH_FAILED: (ACCESSIBILITY, CL19),
    ErrorCode.RATE_LIMITED: (ACCESSIBILITY, CL19),
    ErrorCode.GITHUB_ERROR: (ACCESSIBILITY, CL19),
    ErrorCode.HISTORY_FAILED: ("history read failure", None),
}


class UnitStopped(Exception):
    """A repository or unit stopped on a failure: exit 4, or an unresolved exit code.

    ``category`` names the kind of failure. ``exit_code`` is EXIT_INCOMPLETE for a clone or
    network failure and None otherwise; then ``open_item`` is the open decision that blocks
    the code (CL-19), or None when no source defines one at all.
    """

    def __init__(self, error_type: str, cause: str, action: str, category: str,
                 open_item: str | None, exit_code: int | None = None) -> None:
        """Keep the three parts, the failure category, the open item and the exit code."""
        self.error_type = error_type
        self.cause = cause
        self.action = action
        self.category = category
        self.open_item = open_item
        self.exit_code = exit_code
        super().__init__(f"{error_type}: {cause} {action}")

    @classmethod
    def wrap(cls, url: str,
             error: TdeaError | TrackingStopped | ClassificationStopped) -> UnitStopped:
        """Return the stop for a structured lower-layer failure, naming the repository."""
        if isinstance(error, ClassificationStopped):
            category, open_item = error.category, None
        elif isinstance(error, TrackingStopped):
            category, open_item = "tracking stop on a binary diff", None
        elif error.code in _INCOMPLETE_CATEGORIES:
            return cls(error.error_type, f"{url}: {error.cause}", error.action,
                       _INCOMPLETE_CATEGORIES[error.code], None, EXIT_INCOMPLETE)
        else:
            category, open_item = _STOP_CATEGORIES.get(error.code,
                                                       ("repository failure", None))
        return cls(error.error_type, f"{url}: {error.cause}", error.action, category,
                   open_item)


@dataclass(frozen=True)
class Rg1Rules:
    """The CL-03 components an RG1 run uses: provenance, package filter and bulk lists."""

    classifier: Classifier = field(default_factory=ProvenanceClassifier)
    package_filter: PackageRules = field(default_factory=PackageFilter)
    bulk: BulkRules = field(default_factory=Cl03BulkRules)

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the version, completeness and hash of every list the run uses."""
        return (*self.classifier.list_versions(), self.package_filter.list_version(),
                *self.bulk.list_versions())

    def incomplete(self) -> tuple[str, ...]:
        """Return the names of the lists that are not complete."""
        return tuple(version.name for version in self.list_versions() if not version.complete)


class GitHubAccess:
    """The real FR-03 check over the GitHub API; clones from the HTTPS address (FR-06)."""

    def __init__(self, client: GitHubClient | None = None) -> None:
        """Use ``client`` for the accessibility check."""
        self._client = client if client is not None else GitHubClient()

    def check(self, repository: RepositoryRef, token: str | None) -> None:
        """Raise TdeaError unless the repository is public and non-empty."""
        self._client.check_repository(repository, token)

    def clone_source(self, repository: RepositoryRef) -> RepositoryRef:
        """Return the repository itself: LocalRepository clones its HTTPS address."""
        return repository


class ResearchEngine:
    """Facade of the research analyzer."""

    def __init__(self, rules: Rg1Rules | None = None, profile: ResearchProfile | None = None,
                 access: RepositoryAccess | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        """Use real components unless test doubles are given."""
        self._rules = rules if rules is not None else Rg1Rules()
        self._profile = profile if profile is not None else ResearchProfile()
        self._access = access if access is not None else GitHubAccess()
        self._clock = clock

    def check_url(self, url: object) -> CheckedUrl:
        """Return the repository a valid URL names, or raise UrlRejected (FR-02)."""
        try:
            repository = UrlValidator.parse(url)
        except TdeaError as exc:
            raise UrlRejected(exc.error_type, exc.cause, exc.action) from None
        return CheckedUrl(str(url), repository.owner, repository.name)

    def check_environment(self, facts: EnvironmentFacts | None = None) -> EnvironmentFacts:
        """Return the environment facts, or raise EnvironmentRefused (NFR-24, exit 3)."""
        return self._profile.check_environment(facts)

    def check_rules(self) -> None:
        """Raise RulesIncomplete while any CL-03 list the run uses is incomplete."""
        incomplete = self._rules.incomplete()
        if incomplete:
            raise RulesIncomplete(incomplete)

    def smell_counter(self, smell_mapping: tuple[tuple[str, tuple[str, ...]], ...]
                      ) -> SmellCounter:
        """Return FR-61's counter over this engine's research profile."""
        return SmellCounter(self._profile, smell_mapping)

    def profile_hash(self) -> str:
        """Return the research profile hash (FR-51)."""
        return self._profile.profile_hash()

    def run_metadata(self, facts: EnvironmentFacts) -> dict[str, object]:
        """Return what FR-52 records about the profile, tools, diff and rule lists."""
        diff = research_diff_settings()
        lists = self._rules.list_versions()
        return {
            **self._profile.metadata(facts),
            "git_version": diff["git_version"],
            "gitpython_version": diff["gitpython_version"],
            "rename_threshold": RENAME_THRESHOLD,
            "rule_list_versions": {version.name: {"version": version.version,
                                                  "complete": version.complete,
                                                  "sha256": version.sha256}
                                   for version in lists},
            "prepare_module_version": PREPARE_MODULE_VERSION,
        }

    def discard_stale_clones(self, folder: Path) -> int:
        """Delete clones a killed run left in ``folder``; the caller holds the lock (FR-10)."""
        return remove_stale_clones(folder)

    def open_repository(self, plan: RepositoryPlan, *, key: bytes, token: str | None = None,
                        lint_cache: LintCachePort | None = None,
                        clone_root: Path | None = None) -> RepositorySession:
        """Check, clone (into ``clone_root``) and read one repository; the caller closes it."""
        pseudonymiser = Pseudonymiser(key)
        repository = UrlValidator.parse(plan.url)
        try:
            self._access.check(repository, token)
        except TdeaError as exc:
            raise UnitStopped.wrap(plan.url, exc) from None
        local = LocalRepository(clone_root, clock=self._clock)
        try:
            local.clone(self._access.clone_source(repository))
            return self._session(plan, local, pseudonymiser, lint_cache)
        except TdeaError as exc:
            local.cleanup()
            raise UnitStopped.wrap(plan.url, exc) from None
        except CL03_UNRESOLVED as exc:
            local.cleanup()
            raise RulesIncomplete(self._rules.incomplete(), str(exc)) from None
        except BaseException:
            local.cleanup()
            raise

    def run_rg1_unit(self, session: RepositorySession, window: UnitWindow,
                     w_months: int) -> UnitOutcome:
        """Analyse one RG1 unit: selection, lint, introductions, tracking, modes (7-11)."""
        try:
            return session.run_unit(window, w_months)
        except (TdeaError, TrackingStopped, ClassificationStopped) as exc:
            raise UnitStopped.wrap(session.record.url, exc) from None
        except CL03_UNRESOLVED as exc:
            raise RulesIncomplete(self._rules.incomplete(), str(exc)) from None

    def _session(self, plan: RepositoryPlan, local: LocalRepository,
                 pseudonymiser: Pseudonymiser,
                 lint_cache: LintCachePort | None) -> RepositorySession:
        """Read the clone's history and records, refusing a changed history (FR-49)."""
        reader = HistoryReader(local)
        metadata = reader.clone_metadata()
        if plan.recorded is not None:
            recorded = plan.recorded
            head, cutoff = recorded.head_sha, recorded.cutoff
            clone_time, branch = recorded.clone_time, recorded.default_branch
        else:
            head = plan.pinned_head if plan.pinned_head is not None else metadata.head_sha
            cutoff = plan.cutoff if plan.cutoff is not None else metadata.cloned_at
            clone_time, branch = metadata.cloned_at, metadata.default_branch
        if not reader.contains_commit(head):
            raise HistoryChanged(plan.url, head, plan.recorded is not None)
        history = reader.first_parent(head, cutoff)
        source = HistorySource(reader, self._profile, lint_cache)
        statuses = {commit.sha: self._rules.classifier.classify(commit.raw)
                    for commit in history.commits}
        records = tuple(commit_record(commit, statuses[commit.sha], pseudonymiser,
                                      source.file_changes(commit.sha),
                                      self._rules.package_filter.is_package)
                        for commit in history.commits)
        record = RepositoryRecord(plan.url, branch, clone_time, history.head, cutoff, records)
        return RepositorySession(SessionParts(local, source, history, record, statuses),
                                 self._rules.package_filter, self._rules.bulk)
