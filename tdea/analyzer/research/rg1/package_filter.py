"""PackageFilter: which changed files are package code (FR-51 d).

Package code is the Python (``.py``) files that pass the versioned package-code filter,
which excludes test, documentation, example, vendored, generated and migration files
(glossary, FR-51 d). The ``.py`` part is frozen and applied here. How a CL-03 package-filter
entry (``test/``, ``__tests__/``, ``*_test.py`` in v0.1) matches a path is not defined by
any authoritative source, and Task 6 decision 2 keeps it unresolved: the filter takes a
PathMatcher for it, and the default, UnresolvedPathMatcher, raises FilterRulesUnresolved
instead of guessing. A finalised CL-03 supplies its matcher and list without changes here.
"""

from __future__ import annotations

from typing import Protocol

from tdea.analyzer.research.rg1.provenance import ListVersion
from tdea.analyzer.research.rule_lists import PACKAGE_FILTER, RuleList

PYTHON_SUFFIX = ".py"


class FilterRulesUnresolved(NotImplementedError):
    """Raised instead of filtering while CL-03's path-matching rules are undefined."""


class PathMatcher(Protocol):
    """How package-filter entries match a path: the part a finalised CL-03 must define."""

    def excluding_entries(self, path: str, rules: RuleList) -> tuple[str, ...]:
        """Return the IDs of the entries that exclude the repository-relative ``path``."""


class UnresolvedPathMatcher:
    """The CL-03 v0.1 state: no authoritative path-matching rule exists."""

    reason = (
        "CL-03 v0.1 does not define how its package-filter entries match a path "
        "(directory or file name, depth, letter case); FR-51 (d) cannot filter until the "
        "finalised CL-03 supplies these rules."
    )

    def excluding_entries(self, _path: str, _rules: RuleList) -> tuple[str, ...]:
        """Refuse: path matching is undefined in CL-03 v0.1."""
        raise FilterRulesUnresolved(self.reason)


class PackageFilter:
    """Decides whether a repository path is package code."""

    def __init__(self, rules: RuleList = PACKAGE_FILTER,
                 matcher: PathMatcher | None = None) -> None:
        """Use ``rules``; without a matcher, any CL-03 decision is refused (CL-03 v0.1)."""
        self._rules = rules
        self._matcher = matcher if matcher is not None else UnresolvedPathMatcher()

    def list_version(self) -> ListVersion:
        """Return the list's version, completeness and hash, as FR-52 records them."""
        return ListVersion(self._rules.name, self._rules.version, self._rules.complete,
                           self._rules.content_hash())

    def is_package(self, path: str) -> bool:
        """Return True if ``path`` is package code; a non-.py path never is."""
        if not path.endswith(PYTHON_SUFFIX):
            return False
        return not self._matcher.excluding_entries(path, self._rules)
    