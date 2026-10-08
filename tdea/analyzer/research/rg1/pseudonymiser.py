"""Pseudonymiser: identity keys and the rewritten message (FR-54, NFR-27, Decision 2).

Identity key = HMAC-SHA-256(pseudonymisation key, normalised value) as 64 lowercase hex
characters. E-mails are trimmed and case-folded; names are NFKC-normalised, trimmed,
whitespace-collapsed and case-folded. An empty value gets no key (None). No ``.mailmap``
is applied.

Message rewrite: an identity trailer ``<Word>-by: Name <e-mail>`` becomes
``<Word>-by: id:<e-mail key>``, or ``<Word>-by: id:none`` when it has no e-mail; any
other e-mail address becomes ``id:<e-mail key>``. Names in free text, @-mentions and
identities inside code excerpts are not detected (risk R5). Commit SHAs and repository
URLs are separate fields and are never passed through here.

The key arrives as bytes from the aggregator's KeyStore; this module never imports it
(NFR-14) and never shows the key. Raw strings are read in memory and only keys leave.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from tdea.analyzer.research.rg1.identity_trailers import EMAIL, parse_trailer
from tdea.git_handler.research_commit import RawCommitStrings

KEY_BYTES = 32


class IdentityKind(Enum):
    """Which normalisation an identity value gets."""

    NAME = "name"
    EMAIL = "email"


@dataclass(frozen=True)
class PseudonymisedStrings:
    """The four identity keys and the rewritten message that replace RawCommitStrings."""

    author_name_key: str | None
    author_email_key: str | None
    committer_name_key: str | None
    committer_email_key: str | None
    message: str


def normalise(value: str, kind: IdentityKind) -> str:
    """Return ``value`` normalised as Decision 2 states for its kind."""
    if kind is IdentityKind.EMAIL:
        return value.strip().casefold()
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class Pseudonymiser:
    """Computes identity keys and rewrites messages under one pseudonymisation key."""

    def __init__(self, key: bytes) -> None:
        """Use the 256-bit ``key`` that the aggregator's KeyStore holds."""
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise ValueError("the pseudonymisation key must be 32 bytes")
        self._key = key

    def __repr__(self) -> str:
        """Never show the key."""
        return "Pseudonymiser(<key withheld>)"

    def identity_key(self, value: str, kind: IdentityKind) -> str | None:
        """Return the 64-hex identity key of ``value``, or None when it normalises to empty."""
        normalised = normalise(value, kind)
        if not normalised:
            return None
        return hmac.new(self._key, normalised.encode("utf-8"), hashlib.sha256).hexdigest()

    def rewrite_message(self, message: str) -> str:
        """Replace identity trailers and e-mail addresses with identity keys."""
        return "\n".join(self._rewrite_line(line) for line in message.split("\n"))

    def pseudonymise(self, raw: RawCommitStrings) -> PseudonymisedStrings:
        """Return the keys and rewritten message that are stored instead of ``raw``."""
        return PseudonymisedStrings(
            author_name_key=self.identity_key(raw.author_name, IdentityKind.NAME),
            author_email_key=self.identity_key(raw.author_email, IdentityKind.EMAIL),
            committer_name_key=self.identity_key(raw.committer_name, IdentityKind.NAME),
            committer_email_key=self.identity_key(raw.committer_email, IdentityKind.EMAIL),
            message=self.rewrite_message(raw.message),
        )

    def _rewrite_line(self, line: str) -> str:
        """Rewrite one message line (split on LF), keeping a carriage return at its end."""
        body = line.removesuffix("\r")
        ending = line[len(body):]
        trailer = parse_trailer(body)
        if trailer is not None:
            key = self.identity_key(trailer.email, IdentityKind.EMAIL)
            return f"{trailer.indent}{trailer.token}: id:{key or 'none'}{ending}"
        return EMAIL.sub(self._email_id, body) + ending

    def _email_id(self, match: re.Match[str]) -> str:
        """Return the ``id:<e-mail key>`` that replaces one e-mail address."""
        return f"id:{self.identity_key(match.group(0), IdentityKind.EMAIL)}"


