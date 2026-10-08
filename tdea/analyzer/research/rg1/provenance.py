"""ProvenanceClassifier: AI-traced, bot or non-traced, from raw commit strings (FR-54).

Frozen rules this module applies:

* The raw author, committer and Co-authored-by strings are classified in memory, before
  any key exists (Decision 2).
* An AI pattern match makes the commit AI-traced, recording the detection method and
  every matching pattern ID. Method precedence: author, committer, trailer.
* Only when no AI pattern matches is the commit a bot commit, if its author (and only its
  author) matches the non-AI bot list (D-15). Otherwise it is non-traced. No commit is
  labelled "human".
* The versions of the lists used are recorded with each result, with their
  completeness, so an incomplete list can never pass for a final one.

Not frozen, and therefore not implemented: how a list entry matches a raw string (which
field, exact or partial, letter case, "[bot]" suffixes). CL-03 v0.1 leaves this to be
defined once CL-03 is complete, and it stays unresolved in Task 5. The classifier takes
a PatternMatcher for it; the default, UnresolvedMatcher, raises MatchingRulesUnresolved,
so no research classification is ever made with guessed rules. A finalised CL-03 will
supply its matcher (and lists) without changes here.

Tool: CL-03 v0.1 pairs no pattern with a tool, so ``tool`` is always None; how a tool is
chosen from matching patterns is also left to the finalised CL-03.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from tdea.analyzer.research.rg1.identity_trailers import parse_trailer
from tdea.analyzer.research.rule_lists import AI_TOOL_PATTERNS, NON_AI_BOTS, RuleList
from tdea.git_handler.research_commit import RawCommitStrings


class Method(Enum):
    """Where an AI pattern matched (FR-54)."""

    AUTHOR = "author"
    COMMITTER = "committer"
    TRAILER = "trailer"


METHOD_PRECEDENCE = (Method.AUTHOR, Method.COMMITTER, Method.TRAILER)


class TraceStatus(Enum):
    """The three FR-54 trace statuses; there is deliberately no "human"."""

    AI_TRACED = "ai_traced"
    BOT = "bot"
    NON_TRACED = "non_traced"


class MatchingRulesUnresolved(NotImplementedError):
    """Raised instead of classifying while CL-03's matching rules are undefined."""


@dataclass(frozen=True, repr=False)
class RawIdentity:
    """One raw identity a pattern may be matched against; memory only, repr withheld."""

    method: Method
    name: str
    email: str

    def __repr__(self) -> str:
        """Show where the identity came from, never the identity."""
        return f"RawIdentity(method={self.method.value!r}, <identity withheld>)"


@dataclass(frozen=True)
class ListVersion:
    """The version, completeness and content hash of one rule list used."""

    name: str
    version: str
    complete: bool
    sha256: str


@dataclass(frozen=True)
class Provenance:
    """The FR-54 result for one commit."""

    status: TraceStatus
    method: Method | None
    pattern_ids: tuple[str, ...]
    tool: str | None
    lists: tuple[ListVersion, ...]


class PatternMatcher(Protocol):
    """How list entries match raw identities: the part a finalised CL-03 must define."""

    def ai_pattern_ids(self, identity: RawIdentity, rules: RuleList) -> tuple[str, ...]:
        """Return the IDs of the AI-tool patterns that match ``identity``."""

    def bot_pattern_ids(self, author: RawIdentity, rules: RuleList) -> tuple[str, ...]:
        """Return the IDs of the non-AI bot entries that match the commit's author."""


class UnresolvedMatcher:
    """The CL-03 v0.1 state: no authoritative matching rule exists, so classifying is refused."""

    reason = (
        "CL-03 v0.1 does not define how its entries match raw strings (field, exact or "
        "partial match, letter case, [bot] suffixes); FR-54 cannot classify until the "
        "finalised CL-03 supplies these rules."
    )

    def ai_pattern_ids(self, _identity: RawIdentity, _rules: RuleList) -> tuple[str, ...]:
        """Refuse: AI-pattern matching is undefined in CL-03 v0.1."""
        raise MatchingRulesUnresolved(self.reason)

    def bot_pattern_ids(self, _author: RawIdentity, _rules: RuleList) -> tuple[str, ...]:
        """Refuse: bot matching is undefined in CL-03 v0.1."""
        raise MatchingRulesUnresolved(self.reason)


def raw_identities(raw: RawCommitStrings) -> tuple[RawIdentity, ...]:
    """Return the author, the committer and each Co-authored-by trailer, in that order."""
    trailers = (parse_trailer(line.removesuffix("\r")) for line in raw.message.split("\n"))
    return (
        RawIdentity(Method.AUTHOR, raw.author_name, raw.author_email),
        RawIdentity(Method.COMMITTER, raw.committer_name, raw.committer_email),
        *(RawIdentity(Method.TRAILER, trailer.name, trailer.email)
          for trailer in trailers if trailer is not None and trailer.is_co_author),
    )


def decide(ai_matches: Mapping[Method, Sequence[str]],
           bot_matches: Sequence[str]) -> tuple[TraceStatus, Method | None, tuple[str, ...]]:
    """Apply FR-54's precedence to match outcomes: (status, method, pattern IDs).

    Any AI match wins over a bot match. The method is the first of author, committer and
    trailer with an AI match; every matching AI pattern ID is kept. Without an AI match,
    a bot match (made on the author only) gives a bot commit; otherwise non-traced.
    """
    ai_ids = sorted({pattern for method in METHOD_PRECEDENCE
                     for pattern in ai_matches.get(method, ())})
    if ai_ids:
        method = next(method for method in METHOD_PRECEDENCE if ai_matches.get(method))
        return TraceStatus.AI_TRACED, method, tuple(ai_ids)
    if bot_matches:
        return TraceStatus.BOT, None, tuple(sorted(set(bot_matches)))
    return TraceStatus.NON_TRACED, None, ()


class ProvenanceClassifier:
    """Classifies commits with versioned lists and a matcher that defines matching."""

    def __init__(self, ai_patterns: RuleList = AI_TOOL_PATTERNS, bots: RuleList = NON_AI_BOTS,
                 matcher: PatternMatcher | None = None) -> None:
        """Use the given lists; without a matcher, classification is refused (CL-03 v0.1)."""
        self._ai_patterns = ai_patterns
        self._bots = bots
        self._matcher = matcher if matcher is not None else UnresolvedMatcher()

    def list_versions(self) -> tuple[ListVersion, ...]:
        """Return the version, completeness and hash of both lists, as FR-52 records them."""
        return tuple(ListVersion(rules.name, rules.version, rules.complete, rules.content_hash())
                     for rules in (self._ai_patterns, self._bots))

    def classify(self, raw: RawCommitStrings) -> Provenance:
        """Classify one commit from its raw strings (FR-54)."""
        identities = raw_identities(raw)
        ai_matches: dict[Method, list[str]] = {method: [] for method in METHOD_PRECEDENCE}
        for identity in identities:
            ai_matches[identity.method].extend(
                self._matcher.ai_pattern_ids(identity, self._ai_patterns))
        bot_matches: tuple[str, ...] = ()
        if not any(ai_matches.values()):
            bot_matches = self._matcher.bot_pattern_ids(identities[0], self._bots)
        status, method, pattern_ids = decide(ai_matches, bot_matches)
        return Provenance(status, method, pattern_ids, None, self.list_versions())
    