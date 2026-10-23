"""ResearchProfile: the pinned research lint profile (FR-51, NFR-24, NFR-25, NFR-26).

The profile (FR-51 a): Python 3.11; Pylint 4.1.1, astroid 4.3.3 and radon 6.0.1, the exact
versions Task 1 recorded (CL-02); an explicit empty rcfile; the frozen disable list
C0103, C0114-C0116, C0301, C0303-C0305, C0411-C0415, E0401, E0611 and R0801; plugins and
extension loading off; JSON output. CL-04: E0402 is not in the frozen list, so it stays
enabled. RM decides at G2 (22 Oct) whether to keep it, and a change there is a profile
change with a new profile hash.

check_environment() (NFR-24) refuses, with exit code 3 and the expected versions named,
unless the interpreter is Python 3.11.x in a virtual environment and Pylint, astroid and
radon have their pinned versions and are loaded from that interpreter's site-packages,
where the isolated (-I) Pylint run finds them too. Versions come from the packages'
``__version__``, imported statically (no importlib, NFR-26 guard). GitPython is never
checked (Task 6 decision 6); the git_handler records its version with the diff settings
(FR-50, FR-52), so this module does not import it.

profile_hash() is SHA-256 of the canonical profile: interpreter major.minor, Pylint,
astroid and radon versions, rcfile content, the sorted disable list and the enabled
extras (none, Task 6 decision 5), as JSON with sorted keys and no spaces. The 120 s limit
is recorded with the profile but is not one of FR-51's hash inputs.

lint() (FR-51 b, c, e; NFR-26) lints one file alone, under its own file name, in an empty
temporary folder, as ``sys.executable -I -m pylint --rcfile=<empty> --disable=<list>
--output-format=json --persistent=n <file>``, with PYTHONPATH and PYLINTRC removed,
PYLINTHOME inside the temporary folder (so Pylint writes nothing elsewhere) and a 120 s
limit. It is the research engine's only process (the no-execution guard's one exception).
lint_in_tree() (FR-61) runs the same process on one file inside a temporary copy of an
RG10 variant, with the smell mapping's rules also enabled; it never uses the cache.

A file fails to lint (FR-51 e) with one of four fixed reasons, never Pylint's own text,
which can contain the temporary folder's path: decode error when the content does not
decode as Python source (PEP 263: BOM, coding line, else UTF-8; decided before Pylint
runs, wherever the bad byte is); syntax error when it decodes but does not parse; pylint
crash for any other fatal message, a usage error, a signal or unreadable output; time-out
past the limit. A failed file keeps no messages.

Each message gives one record: symbol, message ID, category (Pylint's type), message text,
line and scope. The scope is Pylint's own scope name for the node the message is attached
to, so a message inside a lambda keeps Pylint's ``outer.<lambda>`` (Task 6 closure). Pylint
writes no scope for a message it attaches to no node (fixme, bad-indentation,
superfluous-parens, too-many-lines and the like), even inside a function; such a message
takes the enclosing class or function of its line (ScopeResolver, FR-51 c), which is the
empty string at module level. That second rule is an open question for the RM.

In-file pragmas behave as in the Pylint CLI: a ``# pylint: disable=...`` comment in the
linted file suppresses messages, ``disable=all`` or ``skip-file`` leaves an ok result with
no messages, and a ``# pylint: enable=...`` comment can bring back a message of the frozen
disable list for the lines it covers. The profile guarantees only that no configuration
outside the file (a project pylintrc, PYLINTRC, HOME or the current folder) enables a
disabled message; it does not filter what the file itself asks for, and the disable list
is unchanged (Task 6 closure).

Results pass through the LintCachePort the aggregator implements (NFR-25); a cache hit is
returned as stored. The cache is used only after check_environment() has accepted the
environment and with the frozen 120 s limit, so every stored result is one the pinned
profile produced (a hit equals a fresh lint). A failure to start Pylint at all (OSError)
is an environment problem and is raised, never stored as the file's result. Nothing here
imports or runs the linted file.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

import astroid
import pylint
import radon

from tdea.analyzer.research.rg1.scopes import ScopeResolver

PYTHON_VERSION = (3, 11)
PYLINT_VERSION = "4.1.1"
ASTROID_VERSION = "4.3.3"
RADON_VERSION = "6.0.1"
DISABLED_MESSAGES = ("C0103", "C0114", "C0115", "C0116", "C0301", "C0303", "C0304", "C0305",
                     "C0411", "C0412", "C0413", "C0414", "C0415", "E0401", "E0611", "R0801")
RCFILE = ""
ENABLED_EXTRAS: tuple[str, ...] = ()
TIME_LIMIT_SECONDS = 120
EXIT_REFUSED = 3

OK = "ok"
FAILED = "failed"
SYNTAX_ERROR = "syntax error"
DECODE_ERROR = "decode error"
PYLINT_CRASH = "pylint crash"
TIME_OUT = "time-out"
_FATAL_BIT = 1
_USAGE_ERROR_BIT = 32


@dataclass(frozen=True)
class EnvironmentFacts:
    """What NFR-24 checks and records about the interpreter that runs Pylint."""

    python: tuple[int, int, int]
    in_virtual_environment: bool
    pylint: str
    astroid: str
    radon: str
    outside_site_packages: tuple[str, ...] = ()

    @classmethod
    def current(cls) -> EnvironmentFacts:
        """Return the facts of the running interpreter (sys.executable)."""
        return cls(
            python=(sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
            in_virtual_environment=sys.prefix != sys.base_prefix,
            pylint=pylint.__version__,
            astroid=astroid.__version__,
            radon=radon.__version__,
            outside_site_packages=_outside_site_packages(),
        )


def _outside_site_packages() -> tuple[str, ...]:
    """Name the pinned tools imported from outside the interpreter's site-packages.

    The versions above come from this process's imports, which PYTHONPATH or the current
    folder can redirect; the isolated Pylint run ignores both and loads site-packages.
    """
    paths = sysconfig.get_paths()
    roots = [Path(root).resolve()
             for root in {*site.getsitepackages(), paths["purelib"], paths["platlib"]}]
    return tuple(name for name, module in (("Pylint", pylint), ("astroid", astroid),
                                           ("radon", radon))
                 if not any(Path(module.__file__).resolve().is_relative_to(root)
                            for root in roots))


class EnvironmentRefused(Exception):
    """A research run must not start in this environment (NFR-24, exit code 3)."""

    exit_code = EXIT_REFUSED

    def __init__(self, problems: list[str]) -> None:
        """Keep the three FR-05 parts; the cause names what was found and expected."""
        self.error_type = "Research environment refused"
        self.cause = "; ".join(problems) + "."
        self.action = ("Run TDEA from a Python 3.11 virtual environment with "
                       f"pylint=={PYLINT_VERSION}, astroid=={ASTROID_VERSION} and "
                       f"radon=={RADON_VERSION} installed.")
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


@dataclass(frozen=True)
class LintMessage:
    """One Pylint message under the research profile (FR-51 c)."""

    symbol: str
    message_id: str
    category: str
    message: str
    line: int
    scope: str


@dataclass(frozen=True)
class LintResult:
    """A file's lint status, failure reason and messages (empty when it failed)."""

    status: str
    failure_reason: str | None
    messages: tuple[LintMessage, ...]


class LintCachePort(Protocol):
    """The lint cache the aggregator implements (NFR-25), keyed as lintcache.db is."""

    def get(self, blob_sha: str, file_name: str, profile_hash: str) -> LintResult | None:
        """Return the cached result for the key, or None."""

    def put(self, blob_sha: str, file_name: str, profile_hash: str,
            result: LintResult) -> None:
        """Store the result for the key."""


def canonical_profile() -> dict[str, object]:
    """Return the profile FR-51 hashes, exactly its listed inputs."""
    return {
        "interpreter": f"{PYTHON_VERSION[0]}.{PYTHON_VERSION[1]}",
        "pylint": PYLINT_VERSION,
        "astroid": ASTROID_VERSION,
        "radon": RADON_VERSION,
        "rcfile": RCFILE,
        "disable": sorted(DISABLED_MESSAGES),
        "enabled_extras": sorted(ENABLED_EXTRAS),
    }


def hash_profile(profile: Mapping[str, object]) -> str:
    """Return SHA-256 of a profile as JSON with sorted keys and no spaces."""
    text = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ResearchProfile:
    """Checks the research environment, identifies the profile and lints single files."""

    def __init__(self, time_limit: float = TIME_LIMIT_SECONDS) -> None:
        """Use the frozen 120 s per-file limit; tests may shorten it (never with a cache)."""
        self._time_limit = time_limit
        self._accepted = False

    def check_environment(self, facts: EnvironmentFacts | None = None) -> EnvironmentFacts:
        """Return the environment facts, or raise EnvironmentRefused (NFR-24, exit 3)."""
        facts = facts if facts is not None else EnvironmentFacts.current()
        problems = []
        if facts.python[:2] != PYTHON_VERSION:
            problems.append(f"Python {'.'.join(map(str, facts.python))} "
                            f"(expected {PYTHON_VERSION[0]}.{PYTHON_VERSION[1]}.x)")
        if not facts.in_virtual_environment:
            problems.append("not a virtual environment (expected one)")
        for tool, found, expected in (("Pylint", facts.pylint, PYLINT_VERSION),
                                      ("astroid", facts.astroid, ASTROID_VERSION),
                                      ("radon", facts.radon, RADON_VERSION)):
            if found != expected:
                problems.append(f"{tool} {found} (expected {expected})")
        problems.extend(f"{tool} is loaded from outside the interpreter's site-packages "
                        "(expected the installed one)" for tool in facts.outside_site_packages)
        if problems:
            raise EnvironmentRefused(problems)
        self._accepted = True
        return facts

    def profile_hash(self) -> str:
        """Return the profile hash (FR-51)."""
        return hash_profile(canonical_profile())

    def metadata(self, facts: EnvironmentFacts) -> dict[str, object]:
        """Return what run metadata records about the profile and tools (FR-52, NFR-24)."""
        return {
            "profile": canonical_profile(),
            "profile_hash": self.profile_hash(),
            "time_limit_seconds": self._time_limit,
            "python_version": ".".join(map(str, facts.python)),
            "pylint_version": facts.pylint,
            "astroid_version": facts.astroid,
            "radon_version": facts.radon,
        }

    def pylint_command(self, rcfile: Path, file_name: str,
                       enable: tuple[str, ...] = ()) -> list[str]:
        """Return the Pylint command for one file, relative to the current folder."""
        command = [sys.executable, "-I", "-m", "pylint", f"--rcfile={rcfile}",
                   f"--disable={','.join(DISABLED_MESSAGES)}"]
        if enable:
            command.append(f"--enable={','.join(enable)}")
        return command + ["--output-format=json", "--persistent=n", file_name]

    def lint(self, content: bytes, file_name: str, *, blob_sha: str | None = None,
             cache: LintCachePort | None = None) -> LintResult:
        """Lint one file's content under ``file_name``, through the cache when given."""
        if not file_name or file_name.startswith("-") or any(
                separator in file_name for separator in ("/", "\\")) or file_name in (".", ".."):
            raise ValueError(f"not a plain file name: {file_name!r}")
        use_cache = cache is not None and blob_sha is not None
        if use_cache and not (self._accepted and self._time_limit == TIME_LIMIT_SECONDS):
            raise RuntimeError("The lint cache is used only after check_environment() has "
                               "accepted the environment, with the 120 s limit.")
        if use_cache:
            cached = cache.get(blob_sha, file_name, self.profile_hash())
            if cached is not None:
                return cached
        result = self._lint_fresh(content, file_name)
        if use_cache:
            cache.put(blob_sha, file_name, self.profile_hash(), result)
        return result

    def lint_in_tree(self, tree: Path, target: str, content: bytes,
                     enable: tuple[str, ...] = ()) -> LintResult:
        """Lint ``content`` as the file ``target`` inside a temporary copy of ``tree``.

        FR-61: an RG10 modified module is linted inside its variant copy, with the smell
        mapping's rules enabled in addition to the profile. ``tree`` is copied (symbolic
        links are copied as links and never followed), ``content`` replaces ``target``
        (a relative POSIX path) in the copy, and Pylint runs from the copy's root on that
        one file, exactly as lint() runs it otherwise: isolated, with the explicit empty
        rcfile, so no pylintrc, pyproject.toml or init-hook inside the tree has any
        effect (NFR-26). Results are never cached: the tree is part of the context.
        """
        parts = PurePosixPath(target).parts
        if not parts or PurePosixPath(target).is_absolute() or ".." in parts \
                or parts[0].startswith("-"):
            raise ValueError(f"not a relative path inside the tree: {target!r}")
        if not _decodes(content):
            return LintResult(FAILED, DECODE_ERROR, ())
        with tempfile.TemporaryDirectory(prefix="tdea-lint-") as temporary:
            root = Path(temporary)
            folder = root / "lint"
            shutil.copytree(tree, folder, symlinks=True)
            (folder.joinpath(*parts)).write_bytes(content)
            return self._pylint(root, folder, target, content, enable)

    def _lint_fresh(self, content: bytes, file_name: str) -> LintResult:
        """Run Pylint on the file alone in a new temporary folder, removed afterwards."""
        if not _decodes(content):
            return LintResult(FAILED, DECODE_ERROR, ())
        with tempfile.TemporaryDirectory(prefix="tdea-lint-") as temporary:
            root = Path(temporary)
            folder = root / "lint"
            folder.mkdir()
            (folder / file_name).write_bytes(content)
            return self._pylint(root, folder, file_name, content, ())

    def _pylint(self, root: Path, folder: Path, target: str, content: bytes,
                enable: tuple[str, ...]) -> LintResult:
        """Run the one isolated Pylint process on ``target`` in ``folder`` (NFR-26)."""
        rcfile = root / "empty.pylintrc"
        rcfile.write_bytes(RCFILE.encode("utf-8"))
        home = root / "pylinthome"
        home.mkdir()
        environment = {name: value for name, value in os.environ.items()
                       if name.upper() not in ("PYTHONPATH", "PYLINTRC")}
        environment["PYLINTHOME"] = str(home)
        try:
            completed = subprocess.run(self.pylint_command(rcfile, target, enable), cwd=folder,
                                       env=environment, capture_output=True,
                                       timeout=self._time_limit, check=False)
        except subprocess.TimeoutExpired:
            return LintResult(FAILED, TIME_OUT, ())
        return _interpret(completed.returncode, completed.stdout, content)


def _decodes(content: bytes) -> bool:
    """Return whether ``content`` decodes as Python source (PEP 263)."""
    try:
        encoding, _lines = tokenize.detect_encoding(io.BytesIO(content).readline)
        content.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError):
        return False
    return True


def _interpret(returncode: int, stdout: bytes, content: bytes) -> LintResult:
    """Turn Pylint's exit code and JSON output into a LintResult."""
    if returncode < 0 or returncode & _USAGE_ERROR_BIT:
        return LintResult(FAILED, PYLINT_CRASH, ())
    try:
        raw = json.loads(stdout.decode("utf-8")) if stdout.strip() else []
        reason = _failure_reason(raw)
        lines = [int(item["line"]) for item in raw]
        pylint_scopes = [str(item["obj"] or "") for item in raw]
    except (ValueError, KeyError, TypeError):  # undecodable or unexpected output
        return LintResult(FAILED, PYLINT_CRASH, ())
    if reason is None and returncode & _FATAL_BIT:
        reason = PYLINT_CRASH
    if reason is not None:
        return LintResult(FAILED, reason, ())
    try:
        scopes = ScopeResolver(content)
    except (SyntaxError, ValueError):  # Pylint parsed what Python's parser rejects
        return LintResult(FAILED, SYNTAX_ERROR, ())
    return LintResult(OK, None, tuple(
        LintMessage(str(item["symbol"]), str(item["message-id"]), str(item["type"]),
                    str(item["message"]), line, pylint_scope or scopes.scope_at(line))
        for item, line, pylint_scope in zip(raw, lines, pylint_scopes)))


def _failure_reason(raw: list[dict[str, object]]) -> str | None:
    """Return why Pylint could not lint the file, from its messages, or None."""
    for item in raw:
        text = str(item["message"]).lower()
        if item["message-id"] == "E0001":
            return DECODE_ERROR if "decode" in text else SYNTAX_ERROR
        if item["type"] == "fatal":
            return DECODE_ERROR if item["message-id"] == "F0010" and "encoding" in text \
                else PYLINT_CRASH
    return None
