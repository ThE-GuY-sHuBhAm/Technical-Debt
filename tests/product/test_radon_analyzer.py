"""RadonAnalyzer: radon's raw SLOC, decoded as Python decodes source (D-8; Day 7 P5).

Day 7 builds only the SLOC that D-8 needs; product cyclomatic complexity (FR-12) and the
Maintainability Index (FR-13) are not implemented. These checks run on Python 3.10 to 3.12.
"""

from __future__ import annotations

import pytest
from radon.raw import analyze

from tdea.analyzer.radon_analyzer import RadonAnalyzer, decode_source

SOURCE = 'x = 1\n# a comment\n\ndef f():\n    """Doc."""\n    return x\n'


def test_sloc_is_radons_own_figure() -> None:
    """SLOC equals radon.raw.analyze on the decoded text; comments and blanks excluded."""
    assert RadonAnalyzer().sloc(SOURCE.encode()) == analyze(SOURCE).sloc == 3


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r"])
def test_every_line_ending_gives_the_same_sloc(ending: bytes) -> None:
    """LF, CR LF and CR source give radon's same count."""
    assert RadonAnalyzer().sloc(SOURCE.encode().replace(b"\n", ending)) == 3


def test_pep263_and_bom_decoding() -> None:
    """A coding line and a UTF-8 BOM are honoured, as Python honours them."""
    latin = "# -*- coding: latin-1 -*-\nname = 'café'\n".encode("latin-1")
    assert decode_source(latin).endswith("'café'\n")
    assert RadonAnalyzer().sloc(latin) == 1
    assert decode_source(b"\xef\xbb\xbfx = 1\n") == "x = 1\n"


def test_empty_and_docstring_only_files_have_zero_sloc() -> None:
    """No code lines: SLOC 0 (D-8 then gives density 0.0, as FR-15 does)."""
    assert RadonAnalyzer().sloc(b"") == 0
    assert RadonAnalyzer().sloc(b'"""Only a docstring."""\n') == 0


def test_undecodable_source_raises() -> None:
    """Bytes that are not valid in the declared encoding are refused."""
    with pytest.raises(SyntaxError):  # in the first two lines, PEP 263 detection refuses
        RadonAnalyzer().sloc(b"x = '\xff'\n")
    with pytest.raises(UnicodeDecodeError):  # later, decoding does
        RadonAnalyzer().sloc(b"x = 1\ny = 2\nz = '\xff'\n")
    with pytest.raises(SyntaxError):
        RadonAnalyzer().sloc(b"# -*- coding: no-such-codec -*-\nx = 1\n")
