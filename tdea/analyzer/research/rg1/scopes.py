"""ScopeResolver: the enclosing-scope qualified name of a line (FR-51 c).

The scope of a line is the dotted chain of the classes and functions that enclose it,
such as ``Cart.total`` or ``outer.inner``; a line outside any class or function is at
module level, written as the empty string, as Pylint itself writes it (Task 6 decision
3). A definition covers its lines from the ``def`` or ``class`` line to its last line;
decorators are evaluated in the enclosing scope, so they belong to it. The source is
parsed with Python's ast module from bytes, so a PEP 263 coding line is honoured as it is
by Pylint. Nothing is imported or run (NFR-26).

The resolver works by line, so it does not name lambdas: a lambda shares its line with the
code around it, and only Pylint's node-level scope can tell them apart. The research
profile therefore takes a message's scope from Pylint (``outer.<lambda>`` included) and uses
this resolver only for messages Pylint attaches to no node.

scope_spans() (Task 7 Day 7, FR-56 counterpart, D-1) lists every class, function and lambda
of a version with the qualified name Pylint 4.1.1 gives it, its kind and its lines, so an
item's scope can be found by name. Its naming was checked against Pylint under the
research profile: a lambda is ``<lambda>`` under the scope Pylint puts it in; decorators,
class bases and class keywords belong to the enclosing scope; a function's argument
defaults and annotations belong to the function (Pylint names a default-argument lambda
``outer.<lambda>``). Names can repeat (a property getter and setter, two lambdas in one
function); how a repeated name is resolved is the Day 7 reading R3, provisional and open
for the RM side. Kinds are "class" and "function" (def, async def and lambda), following
FR-51 (c)'s "classes and functions" (R3, C1, provisional). scope_at() is unchanged.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

MODULE_LEVEL = ""
CLASS = "class"
FUNCTION = "function"
LAMBDA = "<lambda>"


@dataclass(frozen=True)
class _Span:
    """The lines one class or function covers, and its qualified name."""

    first: int
    last: int
    depth: int
    name: str


class ScopeResolver:
    """Names the scope that encloses each line of one Python file."""

    def __init__(self, source: bytes | str) -> None:
        """Parse ``source``; a SyntaxError or ValueError propagates to the caller."""
        self._spans = tuple(_spans(ast.parse(source), "", 0))

    def scope_at(self, line: int) -> str:
        """Return the qualified name of the innermost scope holding ``line``."""
        inner = [span for span in self._spans if span.first <= line <= span.last]
        if not inner:
            return MODULE_LEVEL
        return max(inner, key=lambda span: span.depth).name


def _spans(node: ast.AST, prefix: str, depth: int) -> list[_Span]:
    """Return the spans of every class and function under ``node``."""
    spans: list[_Span] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{child.name}"
            spans.append(_Span(child.lineno, child.end_lineno or child.lineno, depth + 1, name))
            spans.extend(_spans(child, f"{name}.", depth + 1))
        else:
            spans.extend(_spans(child, prefix, depth))
    return spans


@dataclass(frozen=True)
class ScopeSpan:
    """A class, function or lambda: its Pylint qualified name, kind and lines (D-1)."""

    name: str
    kind: str
    first: int  # the def or class line (decorators excluded), or the lambda's first line
    last: int


def scope_spans(source: bytes | str) -> tuple[ScopeSpan, ...]:
    """Return every class, function and lambda of ``source``, in source order.

    A SyntaxError or ValueError from parsing propagates to the caller.
    """
    found: list[tuple[int, int, ScopeSpan]] = []
    _scan(ast.iter_child_nodes(ast.parse(source)), "", found)
    return tuple(span for _line, _column, span in sorted(found, key=lambda entry: entry[:2]))


def locate(spans: Iterable[ScopeSpan], name: str, line: int) -> ScopeSpan | None:
    """Return the one span named ``name`` that holds ``line``, or None if none or several do."""
    holding = [span for span in spans if span.name == name and span.first <= line <= span.last]
    return holding[0] if len(holding) == 1 else None


def _scan(nodes: Iterable[ast.AST], prefix: str,
          found: list[tuple[int, int, ScopeSpan]]) -> None:
    """Add the spans under ``nodes``, whose enclosing scope's name prefix is ``prefix``."""
    for node in nodes:
        if isinstance(node, ast.ClassDef):
            _scan((*node.decorator_list, *node.bases, *node.keywords), prefix, found)
            name = f"{prefix}{node.name}"
            _add(found, node, name, CLASS)
            _scan(node.body, f"{name}.", found)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan(node.decorator_list, prefix, found)
            name = f"{prefix}{node.name}"
            _add(found, node, name, FUNCTION)
            returns = (node.returns,) if node.returns is not None else ()
            _scan((node.args, *returns, *node.body), f"{name}.", found)
        elif isinstance(node, ast.Lambda):
            name = f"{prefix}{LAMBDA}"
            _add(found, node, name, FUNCTION)
            _scan(ast.iter_child_nodes(node), f"{name}.", found)
        else:
            _scan(ast.iter_child_nodes(node), prefix, found)


def _add(found: list[tuple[int, int, ScopeSpan]], node: ast.AST, name: str, kind: str) -> None:
    """Record one span with its position, for sorting into source order."""
    line, column = getattr(node, "lineno"), getattr(node, "col_offset")
    last = getattr(node, "end_lineno", None) or line
    found.append((line, column, ScopeSpan(name, kind, line, last)))
