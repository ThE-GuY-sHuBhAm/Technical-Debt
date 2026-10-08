"""Architecture guard for NFR-26: research analyzer code never runs the code it reads.

A static AST scan of tdea/analyzer/research/ (execution plan, Day 3). It rejects:

* any reference to the built-ins exec, eval and __import__: called or not, as an
  attribute (``builtins.exec``) or imported by name (``from builtins import eval``);
* any import of importlib, runpy or subprocess, in any form.

The one exception is the frozen design's isolated Pylint call (FR-51, NFR-26):
tdea/analyzer/research/profile.py, ResearchProfile's module (Day 4), may ``import
subprocess`` and start exactly one process, with one direct ``subprocess.run(...)`` call.
Besides that call it may name only subprocess's exception classes, CompletedProcess and
the PIPE, DEVNULL and STDOUT constants (an allow-list: ``TimeoutExpired`` is needed for
FR-51's 120 s limit), and it may not pass the module or any of its functions around.
The product PylintAnalyzer lives outside analyzer/research/, so it is outside this
guard, as the plan states. A static scan cannot see every dynamic route (``getattr``
with a string, for one); review covers those. The guard runs in every CI test cell.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tdea

FORBIDDEN_NAMES = frozenset({"exec", "eval", "__import__"})
FORBIDDEN_MODULES = frozenset({"importlib", "runpy", "subprocess"})
PROFILE_NAMES = frozenset({"TimeoutExpired", "CalledProcessError", "SubprocessError",
                           "CompletedProcess", "PIPE", "DEVNULL", "STDOUT"})
RESEARCH = ("analyzer", "research")
PROFILE = "profile.py"  # relative to analyzer/research/: ResearchProfile's single Pylint call


def execution_violations(root: Path) -> list[str]:
    """Return one message per forbidden construct under ``root``/analyzer/research/.

    ``root`` is the directory of the ``tdea`` package.
    """
    research = Path(root).joinpath(*RESEARCH)
    violations: list[str] = []
    for path in sorted(research.rglob("*.py")):
        relative = path.relative_to(research).as_posix()
        where = f"tdea/{'/'.join(RESEARCH)}/{relative}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations += [f"{where}:{line}: {what}"
                       for line, what in _forbidden(tree, allow_pylint_call=relative == PROFILE)]
    return violations


def _forbidden(tree: ast.AST, *, allow_pylint_call: bool) -> list[tuple[int, str]]:
    """Return (line, description) for each forbidden construct in ``tree``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append((node.lineno, f"uses {node.id}"))
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append((node.lineno, f"uses .{node.attr}"))
        elif isinstance(node, ast.Import):
            found += [(node.lineno, f"imports {alias.name}") for alias in node.names
                      if alias.name.split(".")[0] in FORBIDDEN_MODULES
                      and not (allow_pylint_call and alias.name == "subprocess"
                               and alias.asname is None)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and module.split(".")[0] in FORBIDDEN_MODULES:
                found.append((node.lineno, f"imports from {module}"))
            found += [(node.lineno, f"imports {alias.name}") for alias in node.names
                      if alias.name in FORBIDDEN_NAMES]
    if allow_pylint_call:
        found += _subprocess_beyond_one_run(tree)
    return found


def _subprocess_beyond_one_run(tree: ast.AST) -> list[tuple[int, str]]:
    """In profile.py, allow one direct subprocess.run call and the allow-listed names."""
    attributes = {id(node.value): node for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                  and node.value.id == "subprocess"}
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    found: list[tuple[int, str]] = []
    runs = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == "subprocess"):
            continue
        attribute = attributes.get(id(node))
        if attribute is None:
            found.append((node.lineno, "uses the subprocess module other than by attribute"))
        elif attribute.attr == "run" and id(attribute) in called:
            runs += 1
            if runs > 1:
                found.append((node.lineno, "makes a second subprocess.run call"))
        elif attribute.attr not in PROFILE_NAMES:
            found.append((node.lineno, f"uses subprocess.{attribute.attr}"))
    return found


def _write(path: Path, text: str) -> None:
    """Create ``path`` with ``text``, making parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skeleton(tmp_path: Path) -> Path:
    """Create an empty tdea package with analyzer/research/ and return its root."""
    root = tmp_path / "tdea"
    for package in ("", "analyzer", "analyzer/research"):
        _write(root / package / "__init__.py", "")
    return root


def test_research_code_has_no_execution() -> None:
    """The installed tdea package's analyzer/research/ code uses none of the constructs."""
    violations = execution_violations(Path(tdea.__file__).parent)
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("source", [
    'exec("value = 1")\n',
    'eval("1 + 1")\n',
    '__import__("os")\n',
    'import builtins\nbuiltins.exec("value = 1")\n',
    "from builtins import eval\n",
    "run = exec\n",
    "import importlib\n",
    "import importlib.util\n",
    "from importlib import import_module\n",
    "import runpy\n",
    "from runpy import run_path\n",
    "import subprocess\n",
    "import subprocess as sp\n",
    "from subprocess import run\n",
])
def test_guard_detects_each_planted_construct(tmp_path: Path, source: str) -> None:
    """Each forbidden construct, planted in a research module, is reported exactly once."""
    root = _skeleton(tmp_path)
    _write(root / "analyzer" / "research" / "rg10" / "planted.py", source)
    violations = execution_violations(root)
    assert len(violations) == 1, violations
    assert violations[0].startswith("tdea/analyzer/research/rg10/planted.py:")


def test_profile_may_make_exactly_one_subprocess_run_call(tmp_path: Path) -> None:
    """ResearchProfile's single Pylint call, with its timeout handling, is allowed."""
    root = _skeleton(tmp_path)
    profile = root / "analyzer" / "research" / PROFILE
    _write(profile, (
        "import subprocess\n\n"
        "try:\n"
        '    subprocess.run(["python", "-I", "-m", "pylint"], stdout=subprocess.PIPE,\n'
        "                   stderr=subprocess.DEVNULL, timeout=120, check=False)\n"
        "except (subprocess.TimeoutExpired, subprocess.SubprocessError):\n"
        "    pass\n"))
    assert not execution_violations(root)


@pytest.mark.parametrize("source", [
    'import subprocess\nsubprocess.run(["a"])\nsubprocess.run(["b"])\n',
    'import subprocess\nsubprocess.Popen(["a"])\n',
    'import subprocess\nsubprocess.check_output(["a"])\n',
    'import subprocess\nstart = subprocess.Popen\n',
    'import subprocess\nstart = subprocess.run\nstart(["a"])\n',
    'import subprocess\nrunner = subprocess\nrunner.run(["a"])\n',
    'from subprocess import run\nrun(["a"])\n',
    'import subprocess as sp\nsp.run(["a"])\n',
    'import subprocess\nsubprocess.run(["a"])\neval("1")\n',
    'import subprocess\nsubprocess.os.system("x")\n',
    'import subprocess\nsubprocess._fork_exec\n',
    'import subprocess\nsubprocess.builtins\n',
])
def test_profile_exception_allows_nothing_more(tmp_path: Path, source: str) -> None:
    """A second process, another spawning function or an alias is reported once."""
    root = _skeleton(tmp_path)
    _write(root / "analyzer" / "research" / PROFILE, source)
    assert len(execution_violations(root)) == 1, source


def test_exception_is_only_for_research_profile(tmp_path: Path) -> None:
    """A profile.py elsewhere in analyzer/research/ gets no exception."""
    root = _skeleton(tmp_path)
    _write(root / "analyzer" / "research" / "rg1" / PROFILE,
           'import subprocess\nsubprocess.run(["a"])\n')
    assert len(execution_violations(root)) == 1


def test_product_analyzer_is_outside_the_guard(tmp_path: Path) -> None:
    """The product PylintAnalyzer (analyzer/, not analyzer/research/) is not scanned."""
    root = _skeleton(tmp_path)
    _write(root / "analyzer" / "pylint_analyzer.py",
           'import subprocess\nsubprocess.run(["pylint"])\nsubprocess.run(["radon"])\n')
    _write(root / "aggregator" / "service.py", "import importlib\n")
    assert not execution_violations(root)
    