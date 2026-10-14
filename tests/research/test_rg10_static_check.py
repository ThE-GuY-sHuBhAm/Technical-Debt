"""StaticChecker boundaries: parsing (D9-6), new imports and redefinitions (D9-5).

Each case builds a modified module with prepare_module from a small variant module and a
one-block output, then checks it. The new-import and redefinition definitions are Day 9
decision D9-5's provisional ones; these tests pin their documented boundaries and
limitations, not a validated measure. Modules are only parsed: nothing is imported or run
(NFR-26). Python 3.11 only, like the rest of tests/research (NFR-24).
"""

from __future__ import annotations

import ast
import warnings

import pytest

from tdea.analyzer.research.rg10.prepare import PreparedModule, prepare_module
from tdea.analyzer.research.rg10.static_check import (StaticChecker, StaticResult,
                                                      VariantUnparsable, WrongInterpreter,
                                                      describe)

MARKER = "# TASK"


def check(variant: str, code: str) -> StaticResult:
    """Insert ``code`` after the marker of ``variant`` and check the modified module."""
    prepared = prepare_module(variant, MARKER, f"```python\n{code}```\n")
    result = StaticChecker().check(variant, prepared)
    assert result.module_sha256 == prepared.module_sha256
    return result


@pytest.mark.parametrize("variant, code, expected", [
    pytest.param("import os\n# TASK\n", "import json\n", ("json",), id="new"),
    pytest.param("import json\n# TASK\n", "import json\n", (), id="already imported"),
    pytest.param("from json import dumps\n# TASK\n", "import json\n", (),
                 id="from-import covers the module"),
    pytest.param("import os\n# TASK\n", "import os.path\n", ("os.path",),
                 id="parent does not cover a submodule"),
    pytest.param("import os.path\n# TASK\n", "import os\n", ("os",),
                 id="submodule does not cover the parent"),
    pytest.param("# TASK\n", "import json, re as regex\n", ("json", "re"),
                 id="each alias, by module name"),
    pytest.param("# TASK\n", "from . import a\nfrom ..pkg import b\nfrom pkg import c\n",
                 (".", "..pkg", "pkg"), id="relative levels"),
    pytest.param("from .pkg import x\n# TASK\n", "from pkg import y\n", ("pkg",),
                 id="relative and absolute differ"),
    pytest.param("# TASK\n", "def f():\n    import json\n    return json\n", ("json",),
                 id="nested in an inserted function"),
    pytest.param("# TASK\n", "import json\nimport json\nimport re\n", ("json", "re"),
                 id="once each"),
    pytest.param("# TASK\n", "import b\nimport a\n", ("b", "a"), id="source order"),
    pytest.param("def g():\n    import json\n    return json\n\n\n# TASK\n", "import json\n", (),
                 id="variant import inside a function counts"),
    pytest.param("# TASK\nimport json\n", "import json\n", (),
                 id="variant import after the marker counts"),
    pytest.param("import json\n# TASK\n", "VALUE = 1\n", (), id="no inserted import"),
])
def test_new_imports(variant: str, code: str, expected: tuple[str, ...]) -> None:
    """D9-5: inserted imports of modules the variant module does not import."""
    result = check(variant, code)
    assert result.parses and result.new_imports == expected


MATCH_VARIANT = (
    "import contextlib\n"
    "try:\n    def a():\n        pass\nexcept ValueError:\n    def b():\n        pass\n"
    "else:\n    def c():\n        pass\nfinally:\n    def d():\n        pass\n"
    "with contextlib.suppress():\n    def e():\n        pass\n"
    "for i in []:\n    def f():\n        pass\nelse:\n    def g():\n        pass\n"
    "while False:\n    def h():\n        pass\n"
    "match i:\n    case 1:\n        def k():\n            pass\n"
    "try:\n    pass\nexcept* ValueError:\n    def m():\n        pass\n"
    "# TASK\n")
MATCH_CODE = "".join(f"def {name}():\n    pass\n" for name in "abcdefghkm")


@pytest.mark.parametrize("variant, code, expected", [
    pytest.param("def total():\n    pass\n\n\n# TASK\n", "def total():\n    return 1\n",
                 ("total",), id="function"),
    pytest.param("class Cart:\n    pass\n\n\n# TASK\n", "class Cart:\n    pass\n", ("Cart",),
                 id="class"),
    pytest.param("async def fetch():\n    pass\n\n\n# TASK\n", "def fetch():\n    pass\n",
                 ("fetch",), id="def over async def"),
    pytest.param("def count():\n    pass\n\n\nclass Cart:\n    size = 0\n    # TASK\n",
                 "def count(self):\n    return 0\n", (), id="other scope: module vs class"),
    pytest.param("class Cart:\n    def count(self):\n        pass\n    # TASK\n",
                 "def count(self):\n    return 0\n", ("Cart.count",), id="same class scope"),
    pytest.param("import sys\nif sys.version_info:\n    def helper():\n        pass\n\n\n# TASK\n",
                 "def helper():\n    pass\n", ("helper",), id="if opens no scope"),
    pytest.param(MATCH_VARIANT, MATCH_CODE, tuple("abcdefghkm"),
                 id="try, with, for, while, match and except* open no scope"),
    pytest.param("def outer():\n    def inner():\n        pass\n    return inner\n\n\n# TASK\n",
                 "def inner():\n    pass\n", (), id="a function body is another scope"),
    pytest.param("total = 1\n# TASK\n", "def total():\n    pass\n", (),
                 id="an assignment is not a definition"),
    pytest.param("def total():\n    pass\n\n\n# TASK\n", "total = lambda: 1\n", (),
                 id="an inserted assignment is not a definition"),
    pytest.param("# TASK\n", "def f():\n    pass\n\n\ndef f():\n    pass\n", (),
                 id="repeated only within the insertion"),
    pytest.param("def cached():\n    pass\n\n\n# TASK\n",
                 "@staticmethod\ndef cached():\n    pass\n", ("cached",), id="decorated"),
    pytest.param("class Cart:\n    def __init__(self):\n        pass\n\n\n# TASK\n",
                 "class Cart:\n    def __init__(self):\n        pass\n",
                 ("Cart", "Cart.__init__"), id="a class and its method"),
    pytest.param("# TASK\n\n\ndef later():\n    pass\n", "def later():\n    pass\n", ("later",),
                 id="defined after the marker"),
    pytest.param("def total():\n    pass\n\n\n# TASK\n", "x = 1\ndef total(): return 1\n",
                 ("total",), id="a one-line definition on the last inserted line"),
])
def test_redefinitions(variant: str, code: str, expected: tuple[str, ...]) -> None:
    """D9-5: inserted definitions whose name the same scope of the variant defines."""
    result = check(variant, code)
    assert result.parses and result.redefinitions == expected


def test_an_empty_block_inserts_nothing_to_flag() -> None:
    """An empty python block inserts no line: both lists are empty, not undefined."""
    variant = "import json\n\n\ndef total():\n    pass\n\n\n# TASK\n"
    prepared = prepare_module(variant, MARKER, "```python\n```\n")
    assert prepared.inserted_range is None
    result = StaticChecker().check(variant, prepared)
    assert (result.parses, result.new_imports, result.redefinitions) == (True, (), ())


@pytest.mark.parametrize("code, error", [
    pytest.param("def f(:\n    pass\n", "SyntaxError: invalid syntax (line 2)", id="syntax"),
    pytest.param("if True:\n\tx = 1\n        y = 2\n",
                 "TabError: inconsistent use of tabs and spaces in indentation (line 4)",
                 id="tabs"),
    pytest.param("x = 1\n    y = 2\n", "IndentationError: unexpected indent (line 3)",
                 id="indent"),
    pytest.param("x = 1\0\n", "SyntaxError: source code string cannot contain null bytes",
                 id="null byte"),
    pytest.param("x = " + "(" * 10000 + "1" + ")" * 10000 + "\n",
                 "SyntaxError: too many nested parentheses (line 2)", id="10,000 parentheses"),
    pytest.param("".join("    " * depth + "if True:\n" for depth in range(101))
                 + "    " * 101 + "pass\n",
                 "IndentationError: too many levels of indentation (line 102)",
                 id="101 indentation levels"),
    pytest.param("x = " + "not " * 10000 + "y\n", "MemoryError", id="10,000 nots"),
    pytest.param("x = a" + ".b" * 10000 + "\n",
                 "RecursionError: maximum recursion depth exceeded during ast construction",
                 id="10,000 attributes"),
])
def test_modules_that_do_not_parse(code: str, error: str) -> None:
    """D9-6: each failure is "does not parse" with its type, message and line; no flags."""
    result = check("# TASK\n", code)
    assert (result.parses, result.parse_error, result.new_imports, result.redefinitions) == (
        False, error, None, None)


def test_a_deep_tree_that_parses_is_walked_without_recursion() -> None:
    """A 1,200-term expression parses, and the checks walk it without exhausting the stack.

    A recursive walk would need over 1,000 frames for it. (CPython's own limit while it
    builds the tree depends on the caller's stack depth; 1,200 stays well inside it.)
    """
    result = check("import os\n# TASK\n", "import json\nVALUE = " + " + ".join(["1"] * 1200)
                   + "\n")
    assert (result.parses, result.new_imports) == (True, ("json",))


def test_warnings_configuration_cannot_change_the_parse() -> None:
    """An invalid escape warns; turned into an error it would fail the parse, but not here."""
    code = "PATTERN = '\\d'\n"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(SyntaxError):
            ast.parse(code)
        result = check("# TASK\n", code)
    assert result.parses and result.parse_error is None


def test_a_variant_module_that_does_not_parse_stops_the_check() -> None:
    """New imports and redefinitions are undefined without the variant's tree: no guess."""
    with pytest.raises(VariantUnparsable):
        check("x = (\n# TASK\n", "1)\n")


def test_an_output_without_a_module_has_nothing_to_check() -> None:
    """Status none or unterminated gives no module (D-17); checking it is a mistake."""
    prepared = prepare_module("# TASK\n", MARKER, "no code here\n")
    assert isinstance(prepared, PreparedModule) and prepared.text is None
    with pytest.raises(ValueError, match="D-17"):
        StaticChecker().check("# TASK\n", prepared)


@pytest.mark.parametrize("version", [(3, 10), (3, 12), (3, 10, 14), (4, 11)])
def test_other_interpreters_are_refused(version: tuple[int, ...]) -> None:
    """Parse results differ between versions, so only Python 3.11 is accepted."""
    with pytest.raises(WrongInterpreter, match=r"Python 3\.11.*Python "
                       + r"\.".join(str(part) for part in version[:2])):
        StaticChecker(version)


def test_python_3_11_is_accepted() -> None:
    """Any 3.11 micro version is accepted, as is the running 3.11 interpreter."""
    StaticChecker((3, 11, 0))
    StaticChecker()


@pytest.mark.parametrize("error, text", [
    (SyntaxError("bad token", ("m.py", 3, 1, "secret source line\n")), "SyntaxError: bad token "
                                                                       "(line 3)"),
    (SyntaxError(), "SyntaxError"),
    (ValueError("source code string cannot contain null bytes"),
     "ValueError: source code string cannot contain null bytes"),
    (MemoryError(), "MemoryError"),
    (RecursionError("too deep"), "RecursionError: too deep"),
])
def test_describe_never_holds_source_text(error: BaseException, text: str) -> None:
    """parse_error is type, message and line; the offending source line is never kept."""
    assert describe(error) == text


def test_the_check_is_deterministic() -> None:
    """The same module gives the same result every time."""
    variant = "import os\n\n\ndef total():\n    pass\n\n\n# TASK\n"
    code = "import json\n\n\ndef total():\n    return json\n"
    assert check(variant, code) == check(variant, code)
