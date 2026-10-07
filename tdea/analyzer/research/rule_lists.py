"""CL-03 rule lists, version 0.1: partial and not validated.

The four versioned lists the research engine needs are AI-tool attribution patterns and
non-AI bots (FR-54), the package-code filter (FR-51 d) and the D-4 bulk lists, config
files and message patterns (FR-56 a1, a3). Their contents must be re-implemented from
P2's published description, never copied from P2's unlicensed code (SRS 5.3).

P2's paper ("Debt Behind the AI Boom", arXiv 2603.28592) publishes only examples. Its
full detection and classification rules exist only in its replication package. So
version 0.1 holds only entries stated word for word in a permitted source:

* P2's paper (Sec. III-A, III-B);
* SRS v2.3 D-4;
* the RG1 pilot protocol, section 2, which names non-AI bots and formatter message words.

Each entry cites its source. Every list is marked incomplete and lists its gaps. P2
pairs none of its example identifiers with a tool, so no entry names a tool. How an
entry is matched (exact or contains, letter case, which field) is defined with the code
that uses it, FR-54 on Day 3, FR-51 on Day 4 and FR-56 on Day 7, once CL-03 is complete.
These lists are not scientifically validated by being implemented here.

Each list has a version and a SHA-256 over its content. Run metadata records both
(FR-52), so a run states exactly which lists it used. A change to any list needs a new
version and a new TDEA version (execution plan, Day 2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

P2 = "P2, arXiv 2603.28592"
P2_ATTRIBUTION = f"{P2}, Sec. III-A, AI Attribution Rules"
P2_FILES = f"{P2}, Sec. III-B"
SRS_D4 = "SRS v2.3, Section 3.1.10, D-4"
RG1_PROTOCOL = "RG1 pilot protocol, section 2"


@dataclass(frozen=True)
class RuleEntry:
    """One list entry, exactly as its source states it."""

    entry_id: str
    signal: str
    value: str
    source: str


@dataclass(frozen=True)
class RuleList:
    """A versioned rule list with its provenance and known gaps."""

    name: str
    version: str
    used_by: str
    complete: bool
    entries: tuple[RuleEntry, ...]
    gaps: tuple[str, ...]

    def content_hash(self) -> str:
        """Return the SHA-256 of the list's canonical JSON, recorded with each run."""
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


AI_TOOL_PATTERNS = RuleList(
    name="ai_tool_patterns",
    version="0.1",
    used_by="FR-54 commit provenance",
    complete=False,
    entries=(
        RuleEntry("AI-001", "actor login", "copilot-swe-agent[bot]", P2_ATTRIBUTION),
        RuleEntry("AI-002", "author email", "noreply@anthropic.com", P2_ATTRIBUTION),
        RuleEntry("AI-003", "author name", "Cursor Agent", P2_ATTRIBUTION),
    ),
    gaps=(
        "P2 reports 29 AI coding tools but publishes only these three examples; the full "
        "rules are in P2's replication package, which may not be copied (SRS 5.3).",
        "P2 names Co-authored-by trailers as a signal but publishes no trailer values.",
        "P2 does not pair any example with a tool, so FR-54 cannot yet record the tool.",
        "P2's Table I names GitHub Copilot, Claude, Cursor, Gemini and Devin without "
        "signatures.",
        "Matching rules are defined with FR-54 (Day 3).",
    ),
)

NON_AI_BOTS = RuleList(
    name="non_ai_bots",
    version="0.1",
    used_by="FR-54 commit provenance (author only, D-15)",
    complete=False,
    entries=(
        RuleEntry("BOT-001", "bot named in the protocol", "dependabot", RG1_PROTOCOL),
        RuleEntry("BOT-002", "bot named in the protocol", "renovate", RG1_PROTOCOL),
        RuleEntry("BOT-003", "bot named in the protocol", "pre-commit-ci", RG1_PROTOCOL),
        RuleEntry("BOT-004", "bot named in the protocol", "github-actions", RG1_PROTOCOL),
    ),
    gaps=(
        "The protocol names these bots but not the account names or e-mails they commit "
        "under.",
        "P2's paper describes no non-AI bot list.",
        "Matching rules are defined with FR-54 (Day 3).",
    ),
)

PACKAGE_FILTER = RuleList(
    name="package_filter",
    version="0.1",
    used_by="FR-51 (d) package-code filter",
    complete=False,
    entries=(
        RuleEntry("PF-001", "test directory", "test/", P2_FILES),
        RuleEntry("PF-002", "test directory", "__tests__/", P2_FILES),
        RuleEntry("PF-003", "test file name", "*_test.py", P2_FILES),
    ),
    gaps=(
        "FR-51 (d) excludes test, documentation, example, vendored, generated and "
        "migration files; P2 publishes path rules only as these three test examples.",
        "P2 also excludes documentation, configuration files, auto-built artifacts and "
        "vendored dependencies without stating their rules.",
        "No rule for documentation, example, vendored, generated or migration files is "
        "published.",
        "Matching rules are defined with FR-51 (Day 4).",
    ),
)

BULK_CONFIG_FILES = RuleList(
    name="bulk_config_files",
    version="0.1",
    used_by="FR-56 (a1), D-4",
    complete=False,
    entries=(
        RuleEntry("BC-001", "file whose lint or formatter sections count", "pyproject.toml",
                  SRS_D4),
        RuleEntry("BC-002", "file whose lint or formatter sections count", "setup.cfg",
                  SRS_D4),
        RuleEntry("BC-003", "file whose lint or formatter sections count", "tox.ini", SRS_D4),
    ),
    gaps=(
        "D-4 (a1) includes dedicated lint and formatter config files but names none.",
        "Which sections of these three files count as lint or formatter sections is not "
        "stated.",
        "Matching rules are defined with FR-56 (Day 7).",
    ),
)

BULK_MESSAGE_PATTERNS = RuleList(
    name="bulk_message_patterns",
    version="0.1",
    used_by="FR-56 (a3), D-4 (case-insensitive)",
    complete=False,
    entries=tuple(
        RuleEntry(f"BM-{index:03d}", "formatter or autofix named in a commit message", word,
                  RG1_PROTOCOL)
        for index, word in enumerate(
            ("black", "ruff", "isort", "autopep8", "pre-commit", "format", "lint"), start=1)
    ),
    gaps=(
        "Whether a word must match whole or may match inside another word (format, lint) "
        "is not stated.",
        "Matching rules are defined with FR-56 (Day 7).",
    ),
)

ALL_LISTS = (AI_TOOL_PATTERNS, NON_AI_BOTS, PACKAGE_FILTER, BULK_CONFIG_FILES,
             BULK_MESSAGE_PATTERNS)


def list_manifest() -> dict[str, dict[str, object]]:
    """Return each list's version, completeness and content hash, for run metadata."""
    return {
        rule_list.name: {
            "version": rule_list.version,
            "complete": rule_list.complete,
            "sha256": rule_list.content_hash(),
        }
        for rule_list in ALL_LISTS
    }