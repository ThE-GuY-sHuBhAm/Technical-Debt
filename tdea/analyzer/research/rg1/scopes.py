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
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

MODULE_LEVEL = ""


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
