"""Run files (FR-48, SRS 4.5.2): reading and validating one, with no side effects.

``tdea research validate`` reads a YAML run file and checks it fully before anything else
happens: it opens no connection, clones nothing and creates no file (FR-48). The rules
FR-48 lists are applied as written: unknown keys, a missing W, a tau other than 0.5, 0.6,
0.7 or 0.8, a window whose start is not before its end, overlapping windows in one
repository, a cut-off in the future, a URL that fails FR-02 (checked by the analyzer's
ResearchEngine, NFR-14) and a run file containing a token (NFR-19) are rejected. Every
problem found is reported, each as error type, cause and action (FR-05, NFR-11); no
message repeats a token or raw exception text.

Where the frozen texts are silent, these readings apply (Task 7 report):

* A key the frozen schema shows is required unless the schema marks it optional or gives
  its default: key_file, cutoff, head and a window's commits are optional (null means
  "not given"); kind, store, w_months, repositories, url, windows, start and end are
  required, and a required key set to null counts as missing.
* A missing tau or seeds is refused as not yet settled (blocked), not guessed: the schema
  shows both without an optional note, but FR-56 and the glossary give tau a default of
  0.7, and FR-48 only records seeds.
* ``lint_cache`` is in SRS v2.3's schema but not in the Freeze's, and which governs is not
  settled (Task 7 decision 3), so the key is refused as not yet supported (blocked).
* RG10 run files are refused as not yet supported: their keys are open (CL-05, CL-06)
  and their validation arrives with FR-58.
* A token is a GitHub access token in a prefixed form (ghp_, gho_, ghu_, ghs_, ghr_ or
  github_pat_ followed by its characters), found in the file's text (comments included)
  or in any key or string value once YAML escapes are resolved. Such a file gets only the
  token problem, so nothing else is echoed. A legacy 40-hex token cannot be told apart
  from a commit SHA and is not detected.
* Window bounds and the cut-off are UTC: a date means 00:00 UTC, a timestamp without an
  offset is read as UTC, and an offset is converted. As text they are YYYY-MM-DD or
  YYYY-MM-DD[T ]HH:MM[:SS[.ffffff]][Z|+HH:MM|-HH:MM], the same on every Python version.
  SHAs are full 40-character lowercase hex, as the history reader records them. A key
  appearing twice in one mapping, a repository listed twice and a SHA listed twice in one
  window are rejected as ambiguous; YAML merge keys are allowed.
* A YAML value that cannot be built (an impossible date such as 2025-02-30, a bad tag) is
  reported as invalid YAML, never as a raw exception.

Run planning, the run identifier and anything that needs a clone (a listed SHA outside
its window or absent from the history) belong to the run command (Day 6).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol

import yaml

from tdea.analyzer.research.engine import UrlRejected

INVALID = "Invalid run file"
UNSUPPORTED = "Not yet supported"

RG1_REQUIRED = ("kind", "store", "seeds", "w_months", "tau", "repositories")
RG1_OPTIONAL = ("key_file", "cutoff")
REPOSITORY_REQUIRED = ("url", "windows")
REPOSITORY_OPTIONAL = ("head",)
WINDOW_REQUIRED = ("start", "end")
WINDOW_OPTIONAL = ("commits",)
UNRESOLVED_KEYS = {
    "lint_cache": ("The key 'lint_cache' is in SRS v2.3's run-file schema but not in the "
                   "Specification Freeze's, and which of the two governs is not yet decided."),
}
TAU_VALUES = (0.5, 0.6, 0.7, 0.8)

UNSETTLED_WHEN_MISSING = {
    "tau": ("tau is missing. The run-file schema shows tau without marking it optional, while "
            "FR-56 and the glossary give tau a default of 0.7; whether that default applies "
            "to a run file is not yet settled.",
            "Set tau explicitly to 0.5, 0.6, 0.7 or 0.8."),
    "seeds": ("seeds is missing. The run-file schema shows seeds without marking it optional, "
              "while FR-48 only records seeds; whether a run file may leave them out is not "
              "yet settled.", "Add seeds, for example seeds: {sampling: 20261005}."),
}

_TOKEN = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_SHA = re.compile(r"[0-9a-f]{40}")
_SHOWABLE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,29}")
_DATE_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TIMESTAMP_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})"
                             r"(?::(\d{2})(?:\.(\d{1,6}))?)?\s*(Z|z|[+-]\d{2}:\d{2})?")


@dataclass(frozen=True)
class Problem:
    """One reason a run file is refused, in the three FR-05 parts."""

    error_type: str
    cause: str
    action: str
    blocked: bool = False  # True: refused because the specification is not yet settled


_TOKEN_PROBLEM = Problem(
    "Run file contains a token",
    "The run file holds what looks like a GitHub access token. Tokens are never read from "
    "or stored in run files (NFR-19).",
    "Remove the token from the file, and pass it through the TDEA_GITHUB_TOKEN environment "
    "variable or --ask-token instead.")


class RunFileInvalid(Exception):
    """The run file cannot be used; ``problems`` lists every reason found."""

    def __init__(self, problems: list[Problem]) -> None:
        """Keep the problems; the first also gives the exception's three parts."""
        if not problems:
            raise ValueError("RunFileInvalid needs at least one problem")
        first = problems[0]
        super().__init__(f"{first.error_type}: {first.cause} {first.action}")
        self.problems = tuple(problems)
        self.error_type, self.cause, self.action = first.error_type, first.cause, first.action


@dataclass(frozen=True)
class WindowSpec:
    """A committer-date window [start, end) in UTC, with its optional SHA list."""

    start: datetime
    end: datetime
    commits: tuple[str, ...] | None


@dataclass(frozen=True)
class RepositorySpec:
    """One repository of an RG1 run file."""

    url: str
    owner: str
    name: str
    head: str | None
    windows: tuple[WindowSpec, ...]


@dataclass(frozen=True)
class RunFile:
    """A validated RG1 run file."""

    kind: str
    store: str
    key_file: str | None
    seeds: Mapping[str, int]
    w_months: int
    tau: float
    cutoff: datetime | None
    repositories: tuple[RepositorySpec, ...]

    @property
    def unit_count(self) -> int:
        """Return the number of units, one per repository and window (NFR-25)."""
        return sum(len(repository.windows) for repository in self.repositories)


class UrlChecker(Protocol):
    """The FR-02 check the analyzer's ResearchEngine provides."""

    def check_url(self, url: object):
        """Return an object with owner and name, or raise UrlRejected."""


def read_run_file(path: Path | str, checker: UrlChecker, now: datetime) -> RunFile:
    """Read and validate the run file at ``path``; raise RunFileInvalid with every problem."""
    text = _read_text(Path(path))
    if _TOKEN.search(text):
        raise RunFileInvalid([_TOKEN_PROBLEM])
    document = _load_yaml(text)
    if _holds_token(document):  # a token written with YAML escapes
        raise RunFileInvalid([_TOKEN_PROBLEM])
    return _Validator(checker, now).run_file(document)


def _holds_token(value: object) -> bool:
    """Return True if a token appears in any key or string value of a parsed document."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if _TOKEN.search(item):
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return False


def _read_text(path: Path) -> str:
    """Return the file's text, or raise RunFileInvalid if it cannot be read as UTF-8."""
    try:
        data = path.expanduser().read_bytes()
    except OSError:
        raise RunFileInvalid([Problem(
            "Run file cannot be read", f"No readable file was found at {path}.",
            "Check the path and give the YAML run file.")]) from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RunFileInvalid([Problem(
            "Run file cannot be read", "The file is not UTF-8 text.",
            "Save the run file as UTF-8 and run validate again.")]) from None


class _UniqueKeyLoader(yaml.SafeLoader):  # pylint: disable=too-many-ancestors
    """A safe YAML loader that refuses a key appearing twice in one mapping."""

    def construct_mapping(self, node, deep=False):
        """Build a mapping, refusing duplicate keys."""
        seen = set()
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue  # a merge key (<<) may repeat; explicit keys override merged ones
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:  # an unhashable key: SafeLoader reports it itself
                continue
            if duplicate:
                raise _DuplicateKey(key, key_node.start_mark.line + 1)
        return super().construct_mapping(node, deep=deep)



class _DuplicateKey(Exception):
    """A key appeared twice in one YAML mapping."""

    def __init__(self, key: object, line: int) -> None:
        """Keep the key and the line it appeared on the second time."""
        super().__init__("duplicate key")
        self.key = key
        self.line = line


def _load_yaml(text: str) -> object:
    """Parse ``text`` as YAML with the safe loader; raise RunFileInvalid on any failure."""
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # nosec: a SafeLoader subclass
    except _DuplicateKey as exc:
        raise RunFileInvalid([Problem(
            INVALID, f"{_shown(exc.key)} appears twice in one mapping "
            f"(line {exc.line}).", "Keep one entry for each key.")]) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise RunFileInvalid([Problem(
            "Run file is not valid YAML", f"The YAML parser stopped{where}.",
            "Correct the YAML syntax and run validate again.")]) from None
    except Exception:  # pylint: disable=broad-exception-caught
        # PyYAML raises plain ValueError, TypeError and the like while building values
        # (an impossible date, a bad tag) and RecursionError on extreme nesting; none of
        # them may reach the user as raw exception text (FR-05).
        raise RunFileInvalid([Problem(
            "Run file is not valid YAML",
            "A value could not be read, for example an impossible date such as 2025-02-30.",
            "Correct the value and run validate again.")]) from None


def _shown(key: object) -> str:
    """Return the subject naming a key: quoted if it is a plain name, else described."""
    if isinstance(key, str) and _SHOWABLE_KEY.fullmatch(key):
        return f"The key '{key}'"
    return "A key that is not a plain name"


class _Validator:
    """Collects every problem of one parsed run file."""

    def __init__(self, checker: UrlChecker, now: datetime) -> None:
        """Use ``checker`` for FR-02 and ``now`` (UTC) for the cut-off rule."""
        self._checker = checker
        self._now = now.astimezone(timezone.utc)
        self.problems: list[Problem] = []

    def add(self, cause: str, action: str, error_type: str = INVALID,
            blocked: bool = False) -> None:
        """Record one problem."""
        self.problems.append(Problem(error_type, cause, action, blocked))

    def run_file(self, document: object) -> RunFile:
        """Validate the whole document and return the run file, or raise RunFileInvalid."""
        if not isinstance(document, dict):
            raise RunFileInvalid([Problem(
                INVALID, "The run file must be a YAML mapping of keys to values.",
                "Start from the run-file schema in SRS 4.5.2.")])
        kind = document.get("kind")
        if kind == "rg10":
            raise RunFileInvalid([Problem(
                UNSUPPORTED, "RG10 run-file validation arrives with RG10 bundle ingestion "
                "(FR-58), and the RG10 run-file keys are still open (CL-05, CL-06).",
                "Use an RG1 run file; RG10 evaluation is not available in this version.",
                blocked=True)])
        if kind != "rg1":
            cause = ("The required key 'kind' is missing." if "kind" not in document
                     else "kind must be rg1 or rg10.")
            raise RunFileInvalid([Problem(INVALID, cause, "Set kind: rg1 for an RG1 run.")])
        self._keys(document, RG1_REQUIRED, RG1_OPTIONAL, "", self._missing_top)
        values = {
            "store": self._text(document.get("store"), "store", "store: research.db"),
            "key_file": self._optional_text(document.get("key_file"), "key_file"),
            "seeds": self._seeds(document.get("seeds")),
            "w_months": self._w_months(document.get("w_months")),
            "tau": self._tau(document.get("tau")),
            "cutoff": self._cutoff(document.get("cutoff")),
            "repositories": self._repositories(document.get("repositories")),
        }
        if self.problems:
            raise RunFileInvalid(self.problems)
        return RunFile(kind="rg1", **values)

    # --- keys -------------------------------------------------------------------------

    def _keys(self, mapping: dict, required: tuple[str, ...], optional: tuple[str, ...],
              where: str, missing: Callable[[str, str], None]) -> None:
        """Report unknown, not-yet-supported and missing keys of one mapping."""
        allowed = required + optional
        for key in mapping:
            if key in UNRESOLVED_KEYS and not where:
                self.add(UNRESOLVED_KEYS[key],
                         f"Remove {key} from the run file until the specification "
                         "settles this key.", UNSUPPORTED, blocked=True)
            elif key not in allowed:
                self.add(f"{where}{_shown(key)} is not part of the RG1 run-file "
                         "schema.", f"Remove it; the allowed keys here are "
                         f"{_listing(allowed)}.")
        for key in required:
            if mapping.get(key) is None:  # absent, or present with no value
                missing(key, where)

    def _missing_top(self, key: str, _where: str) -> None:
        """Report a missing top-level key; W has its own message (FR-48)."""
        if key == "w_months":
            self.add("W is missing: the key 'w_months' is required.",
                     "Add w_months with the window length in calendar months, "
                     "for example w_months: 6.")
        elif key in UNSETTLED_WHEN_MISSING:
            cause, action = UNSETTLED_WHEN_MISSING[key]
            self.add(cause, action, UNSUPPORTED, blocked=True)
        elif key != "kind":
            self.add(f"The required key '{key}' is missing.",
                     f"Add {key}; see the run-file schema in SRS 4.5.2.")

    def _missing_nested(self, key: str, where: str) -> None:
        """Report a missing key of a repository or window."""
        self.add(f"{where}The required key '{key}' is missing.",
                 f"Add {key}; see the run-file schema in SRS 4.5.2.")

    # --- top-level values -------------------------------------------------------------

    def _text(self, value: object, key: str, example: str) -> str:
        """Return a required non-empty string value."""
        if value is not None and (not isinstance(value, str) or not value.strip()):
            self.add(f"{key} must be a file path.", f"Set {key} to a path, for example "
                     f"{example}.")
        return value if isinstance(value, str) else ""

    def _optional_text(self, value: object, key: str) -> str | None:
        """Return an optional non-empty string value (null means the default)."""
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            self.add(f"{key} must be a file path or null.",
                     f"Set {key} to a path, or remove it to use the default.")
            return None
        return value

    def _seeds(self, value: object) -> dict[str, int]:
        """Return the seeds mapping (recorded only; TDEA uses no randomness)."""
        if value is None:
            return {}
        if not isinstance(value, dict) or not all(
                isinstance(name, str) and _is_int(seed) for name, seed in value.items()):
            self.add("seeds must map names to whole numbers.",
                     "Write seeds as a mapping, for example seeds: {sampling: 20261005}.")
            return {}
        return dict(value)

    def _w_months(self, value: object) -> int:
        """Return W in calendar months."""
        if value is not None and (not _is_int(value) or value < 1):
            self.add("w_months must be a whole number of months, 1 or more.",
                     "Set w_months to the window length, for example w_months: 6.")
        return value if _is_int(value) else 0

    def _tau(self, value: object) -> float:
        """Return tau, which must be one of the four stored values."""
        if value is None:
            return 0.0  # reported as a missing key
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or value not in TAU_VALUES:
            self.add("tau must be 0.5, 0.6, 0.7 or 0.8.",
                     "Set tau to one of 0.5, 0.6, 0.7 or 0.8; modes are stored for all four.")
            return 0.0
        return float(value)

    def _cutoff(self, value: object) -> datetime | None:
        """Return the optional cut-off in UTC; it must not lie in the future."""
        if value is None:
            return None
        moment = _moment(value)
        if moment is None:
            self.add("cutoff must be a UTC timestamp or null.",
                     "Write the cut-off as a timestamp such as 2026-01-01T00:00:00Z, or "
                     "remove it to use the clone time.")
        elif moment > self._now:
            self.add("The cut-off lies in the future.",
                     "Remove cutoff to use the clone time, or set a time that has passed.")
        return moment

    # --- repositories and windows -----------------------------------------------------

    def _repositories(self, value: object) -> tuple[RepositorySpec, ...]:
        """Return the repositories, checking each and refusing a repository listed twice."""
        if value is None:
            return ()
        if not isinstance(value, list) or not value:
            self.add("repositories must be a non-empty list.",
                     "List each repository with its url and windows.")
            return ()
        repositories = []
        first_seen: dict[str, int] = {}
        for number, entry in enumerate(value, start=1):
            repository = self._repository(entry, number)
            if repository is None:
                continue
            identity = f"{repository.owner}/{repository.name}".casefold()
            if identity in first_seen:
                self.add(f"Repository {number} names the same repository as repository "
                         f"{first_seen[identity]}.",
                         "List each repository once, with all of its windows.")
            first_seen.setdefault(identity, number)
            repositories.append(repository)
        return tuple(repositories)

    def _repository(self, entry: object, number: int) -> RepositorySpec | None:
        """Return one repository, or None after reporting why it is unusable."""
        where = f"Repository {number}: "
        if not isinstance(entry, dict):
            self.add(f"{where}each repository must be a mapping with url and windows.",
                     "Write the repository as url: ..., windows: [...].")
            return None
        self._keys(entry, REPOSITORY_REQUIRED, REPOSITORY_OPTIONAL, where,
                   self._missing_nested)
        checked = None
        if entry.get("url") is not None:
            try:
                checked = self._checker.check_url(entry["url"])
            except UrlRejected as exc:
                self.add(f"{where}{exc.cause}", exc.action, exc.error_type)
        head = entry.get("head")
        if head is not None and not (isinstance(head, str) and _SHA.fullmatch(head)):
            self.add(f"{where}head must be a full 40-character commit SHA in lowercase hex, "
                     "or null.", "Correct head, or remove it to use the HEAD at clone time.")
            head = None
        windows = self._windows(entry.get("windows"), where)
        if checked is None:
            return None
        return RepositorySpec(str(entry["url"]), checked.owner, checked.name, head, windows)

    def _windows(self, value: object, where: str) -> tuple[WindowSpec, ...]:
        """Return a repository's windows, refusing bad bounds and overlaps."""
        if value is None:
            return ()
        if not isinstance(value, list) or not value:
            self.add(f"{where}windows must be a non-empty list.",
                     "Give each repository at least one window with start and end.")
            return ()
        numbered = []
        for number, entry in enumerate(value, start=1):
            window = self._window(entry, f"{where}window {number}: ")
            if window is not None:
                numbered.append((number, window))
        for index, (first_number, first) in enumerate(numbered):
            for second_number, second in numbered[index + 1:]:
                if first.start < second.end and second.start < first.end:
                    self.add(f"{where}windows {first_number} and {second_number} overlap.",
                             "Make the windows of one repository disjoint; a window may "
                             "start where another ends.")
        return tuple(window for _number, window in numbered)

    def _window(self, entry: object, where: str) -> WindowSpec | None:
        """Return one window, or None after reporting why it is unusable."""
        if not isinstance(entry, dict):
            self.add(f"{where}each window must be a mapping with start and end.",
                     "Write the window as {start: ..., end: ...}.")
            return None
        self._keys(entry, WINDOW_REQUIRED, WINDOW_OPTIONAL, where, self._missing_nested)
        bounds = {}
        for key in WINDOW_REQUIRED:
            if entry.get(key) is not None:
                bounds[key] = _moment(entry[key])
                if bounds[key] is None:
                    self.add(f"{where}{key} must be a date or a UTC timestamp.",
                             f"Write {key} as a date such as 2025-01-01.")
        commits = self._commits(entry, where)
        start, end = bounds.get("start"), bounds.get("end")
        if start is None or end is None:
            return None
        if start >= end:
            self.add(f"{where}the start is not before the end.",
                     "Give each window a start before its end (start inclusive, "
                     "end exclusive).")
            return None
        return WindowSpec(start, end, commits)

    def _commits(self, entry: dict, where: str) -> tuple[str, ...] | None:
        """Return a window's optional SHA list."""
        value = entry.get("commits")
        if value is None:
            return None
        if not isinstance(value, list) or not value or not all(
                isinstance(sha, str) and _SHA.fullmatch(sha) for sha in value):
            self.add(f"{where}commits must be a non-empty list of full 40-character "
                     "commit SHAs in lowercase hex.",
                     "List the full SHAs, or remove commits to select the whole window.")
            return None
        if len(set(value)) != len(value):
            self.add(f"{where}commits lists a SHA more than once.",
                     "List each SHA once.")
            return None
        return tuple(value)


def _is_int(value: object) -> bool:
    """Return True for an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _moment(value: object) -> datetime | None:
    """Return a date or timestamp as an aware UTC datetime, or None if it is neither."""
    if isinstance(value, str):
        value = _parse_moment_text(value.strip())
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time(0), tzinfo=timezone.utc)
    return None


def _parse_moment_text(text: str) -> date | datetime | None:
    """Parse YYYY-MM-DD or YYYY-MM-DD[T ]HH:MM[:SS[.ffffff]][Z|+HH:MM|-HH:MM], else None."""
    try:
        if _DATE_TEXT.fullmatch(text):
            year, month, day = (int(part) for part in _DATE_TEXT.fullmatch(text).groups())
            return date(year, month, day)
        found = _TIMESTAMP_TEXT.fullmatch(text)
        if found is None:
            return None
        year, month, day, hour, minute = (int(part) for part in found.groups()[:5])
        second = int(found.group(6) or 0)
        micro = int((found.group(7) or "0").ljust(6, "0"))
        zone = found.group(8)
        tzinfo = None
        if zone in ("Z", "z"):
            tzinfo = timezone.utc
        elif zone:
            sign = -1 if zone[0] == "-" else 1
            tzinfo = timezone(sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=tzinfo)
    except ValueError:  # an impossible date or offset
        return None


def _listing(keys: tuple[str, ...]) -> str:
    """Return keys as 'a, b and c'."""
    return ", ".join(keys[:-1]) + f" and {keys[-1]}" if len(keys) > 1 else keys[0]
