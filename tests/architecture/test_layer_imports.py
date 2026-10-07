"""Architecture guard for NFR-14: each layer imports only the layer directly below it.

The direction is frozen in the Specification Freeze, section 2:
api -> aggregator -> analyzer -> git_handler. Imports within a layer, and of the
root package ``tdea`` itself, are allowed. The dashboard layer is the React
application; it reaches the api layer over HTTP, so it is outside this check.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import tdea

PACKAGE = "tdea"
ALLOWED = {
    "api": {"aggregator"},
    "aggregator": {"analyzer"},
    "analyzer": {"git_handler"},
    "git_handler": set(),
}


def _layer_of(dotted: str) -> str | None:
    """Return the layer a dotted module name belongs to, or None outside the layers."""
    parts = dotted.split(".")
    if len(parts) >= 2 and parts[0] == PACKAGE and parts[1] in ALLOWED:
        return parts[1]
    return None


def _import_targets(tree: ast.AST, package_parts: list[str]) -> Iterator[tuple[int, str]]:
    """Yield (line, dotted target) for every import, resolving relative imports.

    For ``from X import name`` both X and ``X.name`` are yielded, because ``name``
    may itself be a module (``from tdea import aggregator``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = (node.module or "").split(".")
            else:
                if node.level - 1 >= len(package_parts):
                    continue  # beyond the top-level package; Python itself rejects it
                base = package_parts[: len(package_parts) - (node.level - 1)]
                if node.module:
                    base = base + node.module.split(".")
            yield node.lineno, ".".join(base)
            for alias in node.names:
                yield node.lineno, ".".join(base + [alias.name])


def layer_violations(root: Path) -> list[str]:
    """Return one message per import that reaches a layer outside ALLOWED.

    ``root`` is the directory of the ``tdea`` package.
    """
    root = Path(root)
    violations: list[str] = []
    for layer in sorted(ALLOWED):
        layer_dir = root / layer
        if not layer_dir.is_dir():
            continue
        for path in sorted(layer_dir.rglob("*.py")):
            module_parts = list(path.relative_to(root.parent).with_suffix("").parts)
            package_parts = module_parts[:-1]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            seen: set[tuple[int, str]] = set()
            for lineno, target in _import_targets(tree, package_parts):
                target_layer = _layer_of(target)
                if target_layer is None or target_layer == layer:
                    continue
                if target_layer in ALLOWED[layer] or (lineno, target_layer) in seen:
                    continue
                seen.add((lineno, target_layer))
                where = path.relative_to(root.parent).as_posix()
                violations.append(
                    f"{where}:{lineno}: {layer} imports {target} "
                    f"(allowed: {sorted(ALLOWED[layer]) or 'none'})"
                )
    return violations


def _write(path: Path, text: str) -> None:
    """Create ``path`` with ``text``, making parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skeleton(tmp_path: Path) -> Path:
    """Create an empty five-layer ``tdea`` package under ``tmp_path`` and return it."""
    root = tmp_path / PACKAGE
    _write(root / "__init__.py", "")
    for layer in ALLOWED:
        _write(root / layer / "__init__.py", "")
    return root


def test_no_layer_violations() -> None:
    """The installed tdea package has no import that crosses the allowed direction."""
    root = Path(tdea.__file__).parent
    violations = layer_violations(root)
    assert not violations, "\n".join(violations)


def test_guard_detects_planted_violation(tmp_path: Path) -> None:
    """A git_handler module importing the aggregator is reported exactly once."""
    root = _skeleton(tmp_path)
    _write(root / "git_handler" / "bad.py", "import tdea.aggregator\n")
    violations = layer_violations(root)
    assert len(violations) == 1
    assert "git_handler imports tdea.aggregator" in violations[0]


def test_guard_resolves_relative_and_upward_imports(tmp_path: Path) -> None:
    """Relative imports are resolved: one adjacent import passes, two violations are caught."""
    root = _skeleton(tmp_path)
    _write(root / "api" / "ok.py", "from ..aggregator import service\n")
    _write(root / "api" / "skip.py", "from ..analyzer import engine\n")
    _write(root / "analyzer" / "up.py", "from .. import aggregator\n")
    violations = layer_violations(root)
    assert len(violations) == 2
    assert any("api imports tdea.analyzer" in v for v in violations)
    assert any("analyzer imports tdea.aggregator" in v for v in violations)
