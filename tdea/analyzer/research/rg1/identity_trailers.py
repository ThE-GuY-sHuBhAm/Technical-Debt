"""Identity trailers and e-mail addresses in commit messages (glossary, Decision 2).

An identity trailer is a message line of the form ``<Word>-by: Name <e-mail>``, such as
Co-authored-by or Signed-off-by. The trailer key is matched without regard to letter
case, as git treats trailer keys, so ``Co-Authored-By`` is an identity trailer too. A
trailer's e-mail is the text inside its last pair of angle brackets; without brackets,
the first e-mail address in the value. E-mail addresses elsewhere are found with a broad
``local@domain`` pattern, because NFR-27 asks for any e-mail address: letters of any
script (combining accents included), digits and ``. _ % + -``, a local part that starts
with a letter or digit, and a domain with or without a dot (unconfigured git writes
``user@host``). It therefore also replaces e-mail-shaped text that is not an address,
such as ``checkout@v4`` or ``ruff@0.1.0``. Quoted local parts, IP-literal domains and a
full-width at sign are not detected. The frozen texts define neither pattern further, so
both are implementation details recorded in the Task 5 report.

This module only parses. Whether a trailer marks AI provenance is decided by FR-54's
versioned lists, and the rewrite is the Pseudonymiser's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TRAILER = re.compile(r"^(?P<indent>[ \t]*)(?P<token>[A-Za-z][A-Za-z0-9-]*-by)[ \t]*:"
                     r"(?P<value>.*)$", re.IGNORECASE)
_MARKS = "\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f"  # combining
_ALNUM = f"(?:[^\\W_][{_MARKS}]*)"
_LABEL = f"{_ALNUM}(?:[\\w{_MARKS}-]*{_ALNUM})?"
EMAIL = re.compile(f"{_ALNUM}[\\w{_MARKS}.%+-]*@{_LABEL}(?:\\.{_LABEL})*")
_BRACKETED = re.compile(r"<([^<>]*)>")
CO_AUTHORED_BY = "co-authored-by"


@dataclass(frozen=True, repr=False)
class IdentityTrailer:
    """One identity trailer line, split into its parts; raw values, so repr is withheld."""

    indent: str
    token: str
    name: str
    email: str

    def __repr__(self) -> str:
        """Show the trailer key only; the name and e-mail never appear."""
        return f"IdentityTrailer(token={self.token!r}, <identity withheld>)"

    @property
    def is_co_author(self) -> bool:
        """Return True for a Co-authored-by trailer, in any letter case."""
        return self.token.casefold() == CO_AUTHORED_BY


def parse_trailer(line: str) -> IdentityTrailer | None:
    """Return the identity trailer on ``line`` (without its line ending), or None."""
    match = TRAILER.match(line)
    if match is None:
        return None
    value = match.group("value")
    bracketed = _BRACKETED.findall(value)
    if bracketed:
        email = bracketed[-1].strip()
        name = value[:value.rfind("<")].strip()
    else:
        found = EMAIL.search(value)
        email = found.group(0) if found else ""
        name = (value[:found.start()] + value[found.end():]).strip() if found else value.strip()
    return IdentityTrailer(match.group("indent"), match.group("token"), name, email)
