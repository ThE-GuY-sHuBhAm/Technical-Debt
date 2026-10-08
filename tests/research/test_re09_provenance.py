"""RE-09 (FR-54, NFR-27), Day 3 part: classification and message rewrite.

What this module can and cannot validate today:

* Pseudonymisation and the message rewrite follow the frozen contract (Decision 2). They
  are checked in full against an HMAC-SHA-256 computed independently here.
* Classification needs a rule for how CL-03 entries match raw strings, and no
  authoritative source defines one yet (CL-03 v0.1 is incomplete). The classifier
  therefore refuses to classify (MatchingRulesUnresolved), and every RE-09 assertion that
  needs a real classification is a strict expected failure until CL-03 is finalised.
  RE-09 is NOT fully validated.
* Tool: CL-03 v0.1 pairs no pattern with a tool, so the tool stays empty; the "right
  tool" assertion is an expected failure too.
* The precedence algorithm (author, committer, trailer; AI over bot; bot from the author
  only) is tested as mechanics only, on synthetic match outcomes that contain no identity
  and no CL-03 entry (the SYNTHETIC ALGORITHM TESTS below). They validate no provenance
  rule.
* The research.db and log search of RE-09 step 4 needs the research store (Day 4).

The commits are FX-RG1's, plus one in-memory commit for "AI agent with a [bot] author",
whose only identifier is CL-03 v0.1 entry AI-001.
"""

from __future__ import annotations

import hashlib
import hmac

import make_fx_rg1
import pytest
from fx_rg1_clone import ClonedFxRg1

from tdea.analyzer.research.rg1.provenance import (
    METHOD_PRECEDENCE, MatchingRulesUnresolved, Method, ProvenanceClassifier, RawIdentity,
    TraceStatus, decide, raw_identities)
from tdea.analyzer.research.rg1.pseudonymiser import IdentityKind, Pseudonymiser
from tdea.analyzer.research.rule_lists import AI_TOOL_PATTERNS, NON_AI_BOTS
from tdea.git_handler.research_commit import RawCommitStrings

UNRESOLVED = ("CL-03 v0.1 defines no matching rule for its entries (Task 5 decision B); "
              "RE-09 classification stays unvalidated until CL-03 is finalised")
FIXTURE_IDENTITIES = (make_fx_rg1.ASHA, make_fx_rg1.RAVI, make_fx_rg1.JANE,
                      make_fx_rg1.AI_COMMITTER, make_fx_rg1.AI_AUTHOR, make_fx_rg1.DEPENDENCY_BOT)
TRAILER_IDENTITY = ("Claude", "noreply@anthropic.com")  # from make_fx_rg1.AI_TRAILER
AI_BOT_AUTHOR = RawCommitStrings(  # RE-09 "AI agent with a [bot] author"; AI-001's value
    author_name=AI_TOOL_PATTERNS.entries[0].value,
    author_email="ai-agent@example.invalid",
    committer_name=make_fx_rg1.ASHA.name,
    committer_email=make_fx_rg1.ASHA.email,
    message="Apply the agent's change\n",
)


def expected_key(key: bytes, normalised: str) -> str:
    """Return HMAC-SHA-256 of an already normalised value, computed independently."""
    return hmac.new(key, normalised.encode("utf-8"), hashlib.sha256).hexdigest()


def raw_values() -> list[str]:
    """Return every raw name and e-mail in the FX-RG1 history, trailers included."""
    values = [part for identity in FIXTURE_IDENTITIES for part in (identity.name, identity.email)]
    return values + list(TRAILER_IDENTITY)


# --- Classification: needs CL-03 matching rules, which are unresolved -------------------

@pytest.mark.xfail(strict=True, raises=MatchingRulesUnresolved, reason=UNRESOLVED)
@pytest.mark.parametrize("label, status, method", [
    ("trailers", TraceStatus.AI_TRACED, Method.TRAILER),
    ("ai_committer", TraceStatus.AI_TRACED, Method.COMMITTER),
    ("ai_author", TraceStatus.AI_TRACED, Method.AUTHOR),
    ("dependency_bot", TraceStatus.BOT, None),
    ("root", TraceStatus.NON_TRACED, None),
])
def test_fx_rg1_classification(cloned_fx_rg1: ClonedFxRg1, label: str,
                               status: TraceStatus, method: Method | None) -> None:
    """Steps 1-2: AI trailer, committer and author, dependency bot and plain commit."""
    provenance = ProvenanceClassifier().classify(cloned_fx_rg1.commit(label).raw)
    assert (provenance.status, provenance.method) == (status, method)


@pytest.mark.xfail(strict=True, raises=MatchingRulesUnresolved, reason=UNRESOLVED)
def test_ai_agent_with_bot_author_is_ai_traced() -> None:
    """Step 2: an AI agent whose author ends in [bot] is AI-traced, not a bot commit."""
    provenance = ProvenanceClassifier().classify(AI_BOT_AUTHOR)
    assert (provenance.status, provenance.method) == (TraceStatus.AI_TRACED, Method.AUTHOR)


@pytest.mark.xfail(strict=True, raises=(MatchingRulesUnresolved, AssertionError),
                   reason="CL-03 v0.1 pairs no pattern with a tool; tool mapping is incomplete")
def test_ai_traced_commits_record_their_tool(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Step 2: AI-traced commits carry the right tool (needs CL-03's tool mapping)."""
    for label in ("trailers", "ai_committer", "ai_author"):
        assert ProvenanceClassifier().classify(cloned_fx_rg1.commit(label).raw).tool is not None


def test_classifier_refuses_until_matching_rules_exist(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Without CL-03 matching rules, classify() refuses instead of guessing."""
    with pytest.raises(MatchingRulesUnresolved, match="CL-03 v0.1"):
        ProvenanceClassifier().classify(cloned_fx_rg1.commit("root").raw)


def test_no_status_is_human() -> None:
    """Step 3: the only labels are ai_traced, bot and non_traced."""
    assert {status.value for status in TraceStatus} == {"ai_traced", "bot", "non_traced"}


def test_lists_used_are_recorded_as_incomplete() -> None:
    """The classifier reports both lists with version 0.1, incomplete, and their hashes."""
    versions = ProvenanceClassifier().list_versions()
    assert [(v.name, v.version, v.complete) for v in versions] == [
        ("ai_tool_patterns", "0.1", False), ("non_ai_bots", "0.1", False)]
    assert [v.sha256 for v in versions] == [AI_TOOL_PATTERNS.content_hash(),
                                            NON_AI_BOTS.content_hash()]


def test_raw_identities_in_precedence_order(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Author, committer, then each Co-authored-by trailer; their repr shows no identity."""
    identities = raw_identities(cloned_fx_rg1.commit("trailers").raw)
    assert [(i.method, i.name, i.email) for i in identities] == [
        (Method.AUTHOR, make_fx_rg1.RAVI.name, make_fx_rg1.RAVI.email),
        (Method.COMMITTER, make_fx_rg1.RAVI.name, make_fx_rg1.RAVI.email),
        (Method.TRAILER, make_fx_rg1.ASHA.name, make_fx_rg1.ASHA.email),
        (Method.TRAILER, *TRAILER_IDENTITY),
    ]
    shown = repr(identities)
    assert not any(value in shown for value in raw_values())
    signed_off = raw_identities(cloned_fx_rg1.commit("signed_off").raw)
    assert [i.method for i in signed_off] == [Method.AUTHOR, Method.COMMITTER]  # not co-author


# --- SYNTHETIC ALGORITHM TESTS ----------------------------------------------------------
# These feed decide() abstract match outcomes ("X1", "B1"): no identity, no CL-03 entry,
# no matching rule. They test the frozen precedence mechanics only and validate no
# provenance rule.

def test_synthetic_method_precedence_author_committer_trailer() -> None:
    """SYNTHETIC: the first method with a match is recorded; all matching IDs are kept."""
    assert decide({Method.TRAILER: ["X3"], Method.COMMITTER: ["X2"]}, []) == (
        TraceStatus.AI_TRACED, Method.COMMITTER, ("X2", "X3"))
    assert decide({Method.AUTHOR: ["X1"], Method.TRAILER: ["X3", "X1"]}, []) == (
        TraceStatus.AI_TRACED, Method.AUTHOR, ("X1", "X3"))
    assert decide({Method.TRAILER: ["X3"]}, []) == (TraceStatus.AI_TRACED, Method.TRAILER,
                                                     ("X3",))
    assert METHOD_PRECEDENCE == (Method.AUTHOR, Method.COMMITTER, Method.TRAILER)


def test_synthetic_ai_wins_over_bot_and_bot_over_nothing() -> None:
    """SYNTHETIC: any AI outcome beats a bot outcome; a bot outcome alone makes a bot."""
    assert decide({Method.AUTHOR: ["X1"]}, ["B1"])[0] is TraceStatus.AI_TRACED
    assert decide({}, ["B1"]) == (TraceStatus.BOT, None, ("B1",))
    assert decide({method: [] for method in Method}, []) == (TraceStatus.NON_TRACED, None, ())


def test_synthetic_bot_check_sees_the_author_only(cloned_fx_rg1: ClonedFxRg1) -> None:
    """SYNTHETIC: the bot check is asked about the author only (D-15), after every AI check."""
    calls: list[tuple[str, Method]] = []

    class RecordingMatcher:
        """Matches nothing; records which identities each check was asked about."""

        def ai_pattern_ids(self, identity: RawIdentity, _rules: object) -> tuple[str, ...]:
            """Record an AI check."""
            calls.append(("ai", identity.method))
            return ()

        def bot_pattern_ids(self, author: RawIdentity, _rules: object) -> tuple[str, ...]:
            """Record a bot check."""
            calls.append(("bot", author.method))
            return ()

    ProvenanceClassifier(matcher=RecordingMatcher()).classify(
        cloned_fx_rg1.commit("trailers").raw)
    assert calls == [("ai", Method.AUTHOR), ("ai", Method.COMMITTER), ("ai", Method.TRAILER),
                     ("ai", Method.TRAILER), ("bot", Method.AUTHOR)]


# --- Pseudonymisation and message rewrite (frozen, fully checked) -----------------------

def test_identity_key_is_hmac_of_the_normalised_value(
    pseudonymiser: Pseudonymiser, research_key: bytes
) -> None:
    """Keys are HMAC-SHA-256 of the normalised value, 64 lowercase hex characters."""
    key = pseudonymiser.identity_key(" Asha.Rao@Example.INVALID ", IdentityKind.EMAIL)
    assert key == expected_key(research_key, "asha.rao@example.invalid")
    assert len(key) == 64 and key == key.lower()
    assert pseudonymiser.identity_key("  Asha \t  Rao ", IdentityKind.NAME) == expected_key(
        research_key, "asha rao")
    assert pseudonymiser.identity_key("Ａｓｈａ Rao", IdentityKind.NAME) == (
        expected_key(research_key, "asha rao"))  # full-width letters, NFKC
    assert pseudonymiser.identity_key("STRASSE", IdentityKind.NAME) == (
        pseudonymiser.identity_key("straße", IdentityKind.NAME))  # case-folded
    assert pseudonymiser.identity_key(" \t ", IdentityKind.NAME) is None
    assert pseudonymiser.identity_key("", IdentityKind.EMAIL) is None


def test_keys_are_stable_per_key_and_differ_across_keys(
    pseudonymiser: Pseudonymiser, research_key: bytes
) -> None:
    """Same identity and key give the same key; another key or identity gives another."""
    email = make_fx_rg1.ASHA.email
    again = Pseudonymiser(bytes(research_key))
    other = Pseudonymiser(bytes(reversed(research_key)))
    first = pseudonymiser.identity_key(email, IdentityKind.EMAIL)
    assert again.identity_key(email, IdentityKind.EMAIL) == first
    assert other.identity_key(email, IdentityKind.EMAIL) != first
    assert pseudonymiser.identity_key(make_fx_rg1.RAVI.email, IdentityKind.EMAIL) != first


def test_trailers_and_body_email_are_replaced(
    cloned_fx_rg1: ClonedFxRg1, pseudonymiser: Pseudonymiser, research_key: bytes
) -> None:
    """Step 4: identity trailers become id:<e-mail key>; a body e-mail becomes its key."""
    jane = expected_key(research_key, make_fx_rg1.JANE.email)
    raw = cloned_fx_rg1.commit("signed_off").raw.message
    expected = raw.replace(f"Signed-off-by: {make_fx_rg1.JANE.name} <{make_fx_rg1.JANE.email}>",
                           f"Signed-off-by: id:{jane}").replace(make_fx_rg1.JANE.email,
                                                                f"id:{jane}")
    assert pseudonymiser.rewrite_message(raw) == expected
    assert f"Reported by id:{jane} on the mailing list." in expected
    trailers = pseudonymiser.rewrite_message(cloned_fx_rg1.commit("trailers").raw.message)
    assert trailers.endswith(
        f"Co-authored-by: id:{expected_key(research_key, make_fx_rg1.ASHA.email)}\n"
        f"Co-authored-by: id:{expected_key(research_key, TRAILER_IDENTITY[1])}\n")
    assert trailers.startswith("Add core processing\n\nWritten in a pairing session.\n")


def test_rewrite_edge_cases(pseudonymiser: Pseudonymiser, research_key: bytes) -> None:
    """No e-mail gives id:none; any key case counts; SHAs, URLs and CRLF stay as they are."""
    reviewer = expected_key(research_key, "reviewer@example.invalid")
    message = ("Fix 61e9ca84cc7cb5f012541b9bff1502b248a14bb7, see "
               "https://github.com/example/project/pull/7\r\n\r\n"
               "Reviewed-by: Reviewer Without Address\r\n"
               "CO-AUTHORED-BY: Someone <Reviewer@Example.Invalid>\r\n"
               "Acked-by: reviewer@example.invalid")
    assert pseudonymiser.rewrite_message(message) == (
        "Fix 61e9ca84cc7cb5f012541b9bff1502b248a14bb7, see "
        "https://github.com/example/project/pull/7\r\n\r\n"
        "Reviewed-by: id:none\r\n"
        f"CO-AUTHORED-BY: id:{reviewer}\r\n"
        f"Acked-by: id:{reviewer}")


def test_any_email_address_is_replaced(pseudonymiser: Pseudonymiser) -> None:
    """Addresses in other scripts and without a dotted domain are replaced too (NFR-27)."""
    message = ("Thanks jos\u00e9@example.invalid, rene\u0301@example.invalid and "
               "user@b\u00fccher.invalid; built as root@localhost (email=x@example.invalid).\n")
    rewritten = pseudonymiser.rewrite_message(message)
    for address in ("jos\u00e9@example.invalid", "rene\u0301@example.invalid",
                    "user@b\u00fccher.invalid", "root@localhost", "x@example.invalid"):
        assert address not in rewritten
        assert f"id:{pseudonymiser.identity_key(address, IdentityKind.EMAIL)}" in rewritten
    assert rewritten.startswith("Thanks id:") and rewritten.endswith(").\n")
    assert "(email=id:" in rewritten
    assert pseudonymiser.rewrite_message("Use `@dataclass` and @pytest.fixture.\n") == (
        "Use `@dataclass` and @pytest.fixture.\n")


def test_no_raw_fixture_identity_survives_pseudonymisation(
    cloned_fx_rg1: ClonedFxRg1, pseudonymiser: Pseudonymiser
) -> None:
    """Step 4 (in memory): no raw name or e-mail of FX-RG1 is left in any commit's output."""
    outputs = [pseudonymiser.pseudonymise(commit.raw) for commit in cloned_fx_rg1.history.commits]
    dumped = repr(outputs).casefold()
    for value in raw_values():
        assert value.casefold() not in dumped, value
    again = [Pseudonymiser(bytes(range(32))).pseudonymise(commit.raw)
             for commit in cloned_fx_rg1.history.commits]
    assert again == outputs  # stable across runs with the same key
    trailers = outputs[cloned_fx_rg1.fixture.first_parent_shas().index(
        cloned_fx_rg1.fixture.shas["trailers"])]
    assert trailers.author_name_key is not None and trailers.author_email_key is not None


def test_pseudonymiser_needs_a_256_bit_key_and_hides_it(research_key: bytes) -> None:
    """A key of the wrong size is refused; the key never shows in repr."""
    with pytest.raises(ValueError):
        Pseudonymiser(research_key[:31])
    assert research_key.hex() not in repr(Pseudonymiser(research_key))
    