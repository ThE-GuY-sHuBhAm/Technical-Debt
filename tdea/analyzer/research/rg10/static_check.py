"""StaticChecker: FR-59's static checks of one modified module (Task 8 Day 9, Option A).

FR-59: the system shall "parse the module under Python 3.11, flag new imports and
redefinitions, and compute the SHA-256 of the module". The module is parsed with
``ast.parse`` in this process (Day 9 decision D9-6) and is never compiled to code,
imported, executed or evaluated (NFR-26). StaticChecker refuses to start on an
interpreter other than Python 3.11, because parse results differ between versions
(Freeze Decision 1, condition 2: Python 3.12 accepts f-strings that 3.11 rejects);
research runs already refuse other interpreters (NFR-24).

Parsing (D9-6). Warnings are silenced while parsing, so the warnings configuration (for
example warnings turned into errors, which makes an invalid escape sequence a
SyntaxError) cannot change the result. SyntaxError (with IndentationError and TabError),
ValueError, RecursionError and MemoryError mean "does not parse". parse_error is
"<exception type>: <message>", with " (line N)" when Python gives a line; it never holds
the source line. CPython limits nesting (200 parentheses, 100 indentation levels) and
recursion while it builds the tree, so a module nested 10,000 levels deep is recorded as
not parsing. The traversals below use explicit stacks, so a deep tree that does parse
cannot exhaust TDEA's own recursion limit.

New imports and redefinitions (Day 9 decision D9-5: provisional operational definitions,
pending the methodology review; not validated):

* A new import is an ``import`` or ``from ... import`` statement whose line lies in the
  inserted range and whose module no import statement of the variant module names. The
  module of ``import a.b`` is "a.b", one per alias; of ``from .m import x`` it is ".m",
  a dot per relative level. Names are compared exactly, and imports anywhere in the
  variant module count, at any depth.
* A redefinition is a ``def``, ``async def`` or ``class`` whose definition line lies in
  the inserted range and whose name the variant module already gives to a ``def``,
  ``async def`` or ``class`` in the same scope. Scopes follow Python: a function or class
  body opens one; if, for, while, try, with and match do not. A scope is identified by
  the names of its enclosing definitions, so a definition inserted in a class body is
  compared with that class's body in the variant module.
* Each list holds names in source order, each once; "Cart.count" is ``count`` in class
  ``Cart``. Both lists are None when the module does not parse and empty when nothing
  was inserted.

Limitations, for the methodology review: names are compared exactly (``import os`` in the
variant does not cover an inserted ``import os.path``, while ``from json import dumps``
covers an inserted ``import json``); assignments, imports and other bindings are not
definitions; the variant module is taken whole, so a name it defines after the marker
counts as already defined. A variant module that does not parse leaves both undefined,
so the check stops (VariantUnparsable) rather than guess.
"""

from __future__ import annotations

import ast
import sys
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from tdea.analyzer.research.profile import PYTHON_VERSION
from tdea.analyzer.research.rg10.prepare import PreparedModule

PARSE_FAILURES = (SyntaxError, ValueError, RecursionError, MemoryError)
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_STATEMENTS = (ast.stmt, ast.excepthandler, ast.match_case)

Span = tuple[int, int] | None


class WrongInterpreter(RuntimeError):
    """StaticChecker was started on an interpreter other than Python 3.11."""


class VariantUnparsable(ValueError):
    """The variant module does not parse, so new imports and redefinitions are undefined."""


@dataclass(frozen=True)
class StaticResult:
    """The static check of one modified module (FR-59)."""

    parses: bool
    parse_error: str | None
    new_imports: tuple[str, ...] | None
    redefinitions: tuple[str, ...] | None
    module_sha256: str


class StaticChecker:
    """Parses modified modules under Python 3.11 and flags new imports and redefinitions."""

    def __init__(self, version: tuple[int, ...] | None = None) -> None:
        """Refuse any interpreter but Python 3.11; ``version`` overrides the running one."""
        running = tuple(sys.version_info[:2] if version is None else version[:2])
        if running != PYTHON_VERSION:
            expected = ".".join(str(part) for part in PYTHON_VERSION)
            found = ".".join(str(part) for part in running)
            raise WrongInterpreter(f"FR-59 parses modified modules under Python {expected}; "
                                   f"this interpreter is Python {found}")

    @staticmethod
    def check(variant_text: str, prepared: PreparedModule) -> StaticResult:
        """Check ``prepared``'s module against the variant module it was built from."""
        if prepared.text is None or prepared.module_sha256 is None:
            raise ValueError("an output without a modified module has nothing to check (D-17)")
        module, error = parse(prepared.text)
        if module is None:
            return StaticResult(False, error, None, None, prepared.module_sha256)
        variant, _variant_error = parse(variant_text)
        if variant is None:
            raise VariantUnparsable("the variant module does not parse under Python 3.11, so "
                                    "new imports and redefinitions cannot be determined")
        span = prepared.inserted_range
        return StaticResult(True, None, new_imports(variant, module, span),
                            redefinitions(variant, module, span), prepared.module_sha256)


def parse(text: str) -> tuple[ast.Module | None, str | None]:
    """Return (tree, None), or (None, parse_error) when ``text`` does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(text), None
    except PARSE_FAILURES as error:
        return None, describe(error)


def describe(error: BaseException) -> str:
    """Return "<type>: <message> (line N)" for a parse failure, without source text."""
    name = type(error).__name__
    if isinstance(error, SyntaxError):
        message, line = error.msg or "", error.lineno
    else:
        message, line = str(error), None
    text = f"{name}: {message}" if message else name
    return text if line is None else f"{text} (line {line})"


def new_imports(variant: ast.Module, module: ast.Module, span: Span) -> tuple[str, ...]:
    """Return the modules imported in the inserted lines that the variant does not import."""
    if span is None:
        return ()
    known = {name for _line, _column, name in _imports(variant)}
    inserted = sorted((entry for entry in _imports(module) if span[0] <= entry[0] <= span[1]),
                      key=lambda entry: entry[:2])
    return _unique(name for _line, _column, name in inserted if name not in known)


def redefinitions(variant: ast.Module, module: ast.Module, span: Span) -> tuple[str, ...]:
    """Return the definitions in the inserted lines whose name the variant's scope defines."""
    if span is None:
        return ()
    known = {(scope, node.name) for scope, node in _definitions(variant)}
    inserted = sorted(((node.lineno, node.col_offset, scope, node.name)
                       for scope, node in _definitions(module)
                       if span[0] <= node.lineno <= span[1]), key=lambda entry: entry[:2])
    return _unique(".".join(scope + (name,)) for _line, _column, scope, name in inserted
                   if (scope, name) in known)


def _imports(tree: ast.Module) -> Iterator[tuple[int, int, str]]:
    """Yield (line, column, module) for each module an import statement names."""
    for node in ast.walk(tree):  # ast.walk is iterative
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, node.col_offset, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.lineno, node.col_offset, "." * node.level + (node.module or "")


def _definitions(tree: ast.Module) -> Iterator[tuple[tuple[str, ...], ast.AST]]:
    """Yield (scope, node) for every def, async def and class, the scope as a name path."""
    stack: list[tuple[ast.AST, tuple[str, ...]]] = [(tree, ())]
    while stack:
        node, scope = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFINITIONS):
                yield scope, child
                stack.append((child, scope + (child.name,)))
            elif isinstance(child, _STATEMENTS):
                stack.append((child, scope))


def _unique(names: Iterable[str]) -> tuple[str, ...]:
    """Return ``names`` in order, each once."""
    return tuple(dict.fromkeys(names))
