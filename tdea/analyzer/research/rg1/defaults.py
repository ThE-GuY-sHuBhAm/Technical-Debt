"""The frozen defaults FR-56 uses on Day 7, one named constant each (D-1 to D-5, D-13).

Source: Specification Freeze v1.0, "Frozen defaults", and SRS v2.3 section 3.1.10. The RM
side may change any of them until 22 Oct 2026 (G2) without reopening the freeze, so none
is confirmed yet and each lives here, cited, rather than inline. Their values reach
metadata.json with the FR-62 export (Task 7 Day 7 decision: no schema change). D-14
(module-level items skip modes c and d) is a rule, not a value; DisappearanceClassifier
applies it, and the module-level path fails closed while CL-17 is open.
"""

from __future__ import annotations

from fractions import Fraction
from tokenize import COMMENT, DEDENT, INDENT, NEWLINE, NL

TAUS = (0.5, 0.6, 0.7, 0.8)  # FR-56: modes are stored for these four values of tau
COUNTERPART_MIN_SIMILARITY = 0.8  # D-1: "the most similar scope ... with similarity >= 0.8"
SIMILARITY_IGNORED = frozenset({NL, NEWLINE, INDENT, DEDENT, COMMENT})  # D-2
FORMATTING_IGNORED = frozenset({NL, NEWLINE, INDENT, DEDENT})  # D-3 (COMMENT is kept)
NEAR_LINES = 3  # D-5: "near = within 3 lines"
SWEEP_FILES = 20  # D-13: "more than 20 files"
SWEEP_SHARE = Fraction(9, 10)  # D-13: "over 90% of its changed lines formatting-only"
