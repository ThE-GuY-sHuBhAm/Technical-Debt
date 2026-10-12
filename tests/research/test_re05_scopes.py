"""RE-05 (FR-51 c, d): scope names and the package filter.

Step 1 lints scopes.py: its scopes are module level (the empty string, as Pylint writes
it; Task 6 decision 3), ``Cart.total`` and ``outer.inner``, and ScopeResolver agrees with
Pylint's own scope for every message. Step 2 runs the package filter over FX-RG1. CL-03
v0.1 defines no rule for how its entries (``test/``, ``__tests__/``, ``*_test.py``) match a
path, so the filter refuses to decide (Task 6 decision 2). Every step-2 assertion that
needs a decision is a strict expected failure. Even a literal reading of v0.1 could not
exclude FX-RG1's tests/, docs/, examples/, vendor/ and migrations/ folders or its generated
file, which the frozen fixture contains on purpose. FX-RG1 is not changed.
"""

from __future__ import annotations

import make_fx_pairs
import pytest
from fx_rg1_clone import ClonedFxRg1

from tdea.analyzer.research.profile import OK, ResearchProfile
from tdea.analyzer.research.rg1.package_filter import FilterRulesUnresolved, PackageFilter
from tdea.analyzer.research.rg1.scopes import MODULE_LEVEL, ScopeResolver
from tdea.analyzer.research.rule_lists import PACKAGE_FILTER
from tdea.git_handler.history_reader import HistoryReader

UNRESOLVED = ("CL-03 v0.1 defines no path-matching rule for its package filter (Task 6 "
              "decision 2); RE-05 step 2 stays unvalidated until CL-03 is finalised")
EXCLUDED = ("tests/test_util.py", "docs/conf.py", "examples/demo.py", "vendor/thirdparty.py",
            "migrations/0001_initial.py", "pkg/_generated_schema.py")
PACKAGE = ("pkg/__init__.py", "pkg/core.py", "pkg/report.py", "pkg/helpers.py",
           "pkg/feature.py")
NESTED = (
    '"""Nested scopes."""\n'          # 1
    "import functools\n"              # 2
    "\n"                              # 3
    "class Outer:\n"                  # 4
    "    LIMIT = 3\n"                 # 5
    "\n"                              # 6
    "    class Inner:\n"              # 7
    "        def method(self):\n"     # 8
    "            return self\n"       # 9
    "\n"                              # 10
    "    @functools.cache\n"          # 11
    "    def cached(self):\n"         # 12
    "        return 1\n"              # 13
    "\n"                              # 14
    "async def runner():\n"           # 15
    "    return 2\n"                  # 16
    "\n"                              # 17
    "VALUE = 4\n"                     # 18
)


def test_step1_scopes_are_module_level_cart_total_and_outer_inner() -> None:
    """Linting scopes.py gives exactly the three frozen scope names."""
    result = ResearchProfile().lint(make_fx_pairs.SCOPES.encode(), "scopes.py")
    assert result.status == OK
    assert [message.scope for message in result.messages] == ["", "Cart.total", "outer.inner"]
    assert MODULE_LEVEL == ""


@pytest.mark.parametrize("line, scope", [
    (1, ""), (2, ""), (4, "Outer"), (5, "Outer"), (7, "Outer.Inner"),
    (8, "Outer.Inner.method"), (9, "Outer.Inner.method"), (11, "Outer"),
    (12, "Outer.cached"), (13, "Outer.cached"), (15, "runner"), (16, "runner"),
    (18, ""),
])
def test_scope_resolver_names_dotted_classes_and_functions(line: int, scope: str) -> None:
    """Classes, methods, async functions and decorators (enclosing scope) are named."""
    assert ScopeResolver(NESTED.encode()).scope_at(line) == scope


def test_scope_resolver_honours_a_coding_line() -> None:
    """Source in Latin-1 with a PEP 263 coding line is parsed as Pylint parses it."""
    source = b'# -*- coding: latin-1 -*-\n"""Caf\xe9."""\n\n\ndef f():\n    return 1\n'
    assert ScopeResolver(source).scope_at(6) == "f"


def test_step2_non_python_files_are_never_package_code() -> None:
    """Only .py files can be package code (glossary); that part is frozen and applied."""
    package_filter = PackageFilter()
    for path in ("README.md", "requirements.txt", "docs/index.rst", "pkg/data.json"):
        assert package_filter.is_package(path) is False


def test_step2_filter_refuses_until_cl03_defines_matching() -> None:
    """For a .py path the filter refuses rather than invent a matching rule."""
    with pytest.raises(FilterRulesUnresolved, match="CL-03 v0.1"):
        PackageFilter().is_package("pkg/core.py")
    version = PackageFilter().list_version()
    assert (version.name, version.version, version.complete) == ("package_filter", "0.1", False)
    assert version.sha256 == PACKAGE_FILTER.content_hash()


@pytest.mark.xfail(strict=True, raises=FilterRulesUnresolved, reason=UNRESOLVED)
@pytest.mark.parametrize("path", EXCLUDED)
def test_step2_excluded_folders_and_generated_file_are_not_package_code(path: str) -> None:
    """FX-RG1's test, doc, example, vendored, migration and generated files are excluded."""
    assert PackageFilter().is_package(path) is False


@pytest.mark.xfail(strict=True, raises=FilterRulesUnresolved, reason=UNRESOLVED)
@pytest.mark.parametrize("path", PACKAGE)
def test_step2_package_files_are_package_code(path: str) -> None:
    """FX-RG1's pkg/ modules are package code."""
    assert PackageFilter().is_package(path) is True


@pytest.mark.xfail(strict=True, raises=FilterRulesUnresolved, reason=UNRESOLVED)
def test_step2_excluded_files_produce_no_messages(cloned_fx_rg1: ClonedFxRg1) -> None:
    """Linting FX-RG1 at HEAD through the filter gives no message from an excluded file."""
    reader = HistoryReader(cloned_fx_rg1.local)
    head = cloned_fx_rg1.fixture.shas["latest"]
    package_filter = PackageFilter()
    messages = {path: ResearchProfile().lint(reader.read_blob(head, path),
                                             path.rsplit("/", 1)[-1]).messages
                if package_filter.is_package(path) else ()
                for path in EXCLUDED + PACKAGE}
    assert not any(messages[path] for path in EXCLUDED)
    assert any(package_filter.is_package(path) for path in PACKAGE)
    