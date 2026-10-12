"""Test support for research runs on local fixtures (Task 7 Day 6).

SYNTHETIC — not CL-03. A real run is refused while CL-03 is incomplete (Day 6 decision
1), so the run mechanics are exercised with test doubles that decide nothing CL-03
decides: SyntheticClassifier labels commits the test names one by one (by subject line)
and calls every other commit non-traced; SyntheticPackages treats exactly the paths a
test lists as package code. Neither uses a pattern, an identifier from CL-03 or any
matching rule, and both report list names starting "SYNTHETIC — not CL-03", which the
run metadata then records. Results obtained with them are mechanics evidence only, never
evidence for an acceptance check that CL-03 blocks.

LocalAccess stands in for the FR-03 GitHub check and clones a local fixture for a
placeholder github.com URL, so no test reaches the network (FX-RG1 is unpublished); it
records the tokens it is given so tests can check where a PAT goes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tdea.aggregator.research_service import ResearchService, ServicePaths
from tdea.analyzer.research.engine import ResearchEngine, Rg1Rules
from tdea.analyzer.research.profile import EnvironmentFacts, ResearchProfile
from tdea.analyzer.research.rg1.provenance import ListVersion, Provenance, TraceStatus
from tdea.git_handler.errors import TdeaError
from tdea.git_handler.research_commit import RawCommitStrings
from tdea.git_handler.url_validator import RepositoryRef

SYNTHETIC = "SYNTHETIC — not CL-03"
PINNED_FACTS = EnvironmentFacts((3, 11, 9), True, "4.1.1", "4.3.3", "6.0.1")
FX_RG1_URL = "https://github.com/tdea-fixtures/fx-rg1"
FX_PAIRS_URL = "https://github.com/tdea-fixtures/fx-pairs"
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

FX_RG1_PACKAGES = (
    "pkg/__init__.py", "pkg/util.py", "pkg/helpers.py", "pkg/core.py", "pkg/report.py",
    "pkg/feature.py", "pkg/tracked_a.py", "pkg/tracked_alpha.py", "pkg/tracked_b.py",
    "pkg/tracked_c.py", "pkg/tracked_d.py", "pkg/bot_pinned.py", "pkg/feature_flags.py",
)
FX_PAIRS_PACKAGES = tuple(f"pairs/{name}.py" for name in (
    "a_moved", "b_duplicate", "c_two_scopes", "d_whitespace", "e_added_modified_untouched"))
BOT_SUBJECTS = ("Pin versions",)  # FX-RG1 v2's dependency-bot commit that adds a warning


def _version(what: str) -> ListVersion:
    """Return the list version a SYNTHETIC double reports."""
    return ListVersion(f"{SYNTHETIC}: {what}", "test", True, "0" * 64)


class SyntheticClassifier:
    """SYNTHETIC — not CL-03: commits named by subject get a status; others non-traced."""

    def __init__(self, labelled: Mapping[str, TraceStatus] | None = None) -> None:
        """Label the commits whose first message line is a key of ``labelled``."""
        self.labelled = dict(labelled or {})

    def classify(self, raw: RawCommitStrings) -> Provenance:
        """Return the test's label for this commit, never a CL-03 decision."""
        subject = raw.message.split("\n", 1)[0].strip()
        status = self.labelled.get(subject, TraceStatus.NON_TRACED)
        return Provenance(status, None, (), None, self.list_versions())

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the double's list version."""
        return (_version("classifier"),)


class SyntheticPackages:
    """SYNTHETIC — not CL-03: exactly the listed paths are package code."""

    def __init__(self, packages: Iterable[str]) -> None:
        """Treat ``packages`` as package code and nothing else."""
        self.packages = frozenset(packages)

    def is_package(self, path: str) -> bool:
        """Answer from the explicit list."""
        return path in self.packages

    def list_version(self) -> ListVersion:
        """Return the double's list version."""
        return _version("package filter")


@dataclass
class LocalAccess:
    """Not FR-03: accepts any mapped repository and clones its local fixture."""

    sources: dict[str, Path]
    tokens: list[str | None] = field(default_factory=list)
    failure: TdeaError | None = None

    def check(self, repository: RepositoryRef, token: str | None) -> None:
        """Record the token; fail if the test set a failure."""
        self.tokens.append(token)
        if self.failure is not None:
            raise self.failure

    def clone_source(self, repository: RepositoryRef) -> Path:
        """Return the local fixture for the repository."""
        return self.sources[repository.full_name]


def synthetic_rules(bots: Iterable[str] = BOT_SUBJECTS) -> Rg1Rules:
    """Return SYNTHETIC — not CL-03 rules for FX-RG1 v2 and FX-PAIRS."""
    return Rg1Rules(SyntheticClassifier({subject: TraceStatus.BOT for subject in bots}),
                    SyntheticPackages(FX_RG1_PACKAGES + FX_PAIRS_PACKAGES))


class TickingClock:
    """A clock one second later at every call, from NOW: distinct, reproducible times."""

    def __init__(self, start: datetime = NOW) -> None:
        """Start at ``start``."""
        self.now = start

    def __call__(self) -> datetime:
        """Return the next time."""
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class Workspace:
    """A test's folders: run file folder, clones, lock, lint cache, and its access double."""

    root: Path
    access: LocalAccess

    @property
    def clones(self) -> Path:
        """Return the run clone folder the service uses: ``clones`` in the lock folder."""
        return self.paths().lock_directory / "clones"

    def clone_entries(self) -> set[Path]:
        """Return what the run clone folder holds (nothing if it does not exist yet)."""
        return set(self.clones.iterdir()) if self.clones.exists() else set()

    @property
    def store(self) -> Path:
        """Return research.db's path (the run files say store: research.db)."""
        return self.root / "research.db"

    def paths(self) -> ServicePaths:
        """Return the service paths inside the workspace."""
        return ServicePaths(lock_directory=self.root / "lock",
                            lint_cache=self.root / "cache" / "lintcache.db",
                            key_file=self.root / "keys" / "research.key")

    def engine(self, rules: Rg1Rules | None = None,
               profile: ResearchProfile | None = None) -> ResearchEngine:
        """Return an engine with SYNTHETIC rules, the local access and a fixed clone clock."""
        return ResearchEngine(rules if rules is not None else synthetic_rules(), profile,
                              self.access, clock=lambda: NOW)

    def service(self, rules: Rg1Rules | None = None,
                profile: ResearchProfile | None = None) -> ResearchService:
        """Return a service over the workspace with the pinned facts and a ticking clock."""
        return ResearchService(self.engine(rules, profile), TickingClock(),
                               paths=self.paths(), environment=PINNED_FACTS)

    def run_file(self, text: str, name: str = "run.yaml") -> Path:
        """Write a run file into the workspace and return its path."""
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path


def run_file_text(repositories: Iterable[tuple[str, Iterable[str], str | None]],
                  w_months: int = 6, cutoff: str | None = "2026-10-11T00:00:00Z") -> str:
    """Return an RG1 run file: (url, windows as flow mappings, pinned head) per repository."""
    lines = ["kind: rg1", "store: research.db", "seeds: {sampling: 20261005}",
             f"w_months: {w_months}", "tau: 0.7",
             f"cutoff: {cutoff if cutoff is not None else 'null'}", "repositories:"]
    for url, windows, head in repositories:
        lines.append(f"  - url: {url}")
        if head is not None:
            lines.append(f"    head: '{head}'")
        lines.append("    windows:")
        lines.extend(f"      - {window}" for window in windows)
    return "\n".join(lines) + "\n"


FX_RG1_WINDOWS = ("{start: 2026-01-01, end: 2026-04-07}", "{start: 2026-04-07, end: 2026-08-01}")
FX_PAIRS_WINDOWS = ("{start: 2026-05-01, end: 2026-05-03}", "{start: 2026-05-03, end: 2026-06-01}")


def four_units() -> str:
    """Return the RE-01 shape: 2 repositories x 2 windows, W = 6, tau = 0.7."""
    return run_file_text([(FX_RG1_URL, FX_RG1_WINDOWS, None),
                          (FX_PAIRS_URL, FX_PAIRS_WINDOWS, None)])


def dump(store: Path) -> dict[str, list[tuple]]:
    """Return every row of every table of research.db, in a fixed order."""
    with sqlite3.connect(store) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(),
                             key=repr) for name in names}


def rows(store: Path, query: str, *parameters: object) -> list[tuple]:
    """Run one query on research.db and return its rows."""
    with sqlite3.connect(store) as connection:
        return connection.execute(query, parameters).fetchall()
