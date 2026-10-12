"""RadonAnalyzer: radon's raw source-lines count (SLOC), the one radon figure research uses.

D-8 defines baseline density as "research-profile messages in the parent version ÷ its
SLOC (radon, as in FR-15)", so the research engine needs radon's SLOC and nothing else.
Task 7 Day 7 decision: only raw SLOC is built. The product's cyclomatic complexity
(FR-12) and Maintainability Index (FR-13) are not implemented here, and nothing is wired
into the product pipeline; FR-11's per-commit product metrics remain later product work.

The source is decoded as Python decodes it (PEP 263 coding line or UTF-8, a UTF-8 BOM
removed) and given to radon.raw.analyze unchanged; radon's ``sloc`` is returned as radon
computes it (Task 7 Day 7, P5, provisional). Radon reads the text; it never imports or
runs it. A file that cannot be decoded raises SyntaxError or UnicodeDecodeError.
"""

from __future__ import annotations

import io
import tokenize

from radon.raw import analyze


def decode_source(content: bytes) -> str:
    """Return Python source bytes as text, decoded by PEP 263 rules."""
    encoding, _consumed = tokenize.detect_encoding(io.BytesIO(content).readline)
    return content.decode(encoding)


class RadonAnalyzer:
    """Radon's raw metrics for one file version; only SLOC is offered (D-8)."""

    def sloc(self, content: bytes) -> int:
        """Return radon's source lines of code for one file version."""
        return analyze(decode_source(content)).sloc
