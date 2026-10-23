"""BundleLoader: read and validate one RG10 bundle (FR-58; Task 8 Day 9, Option A).

The bundle is the research harness's directory, laid out as Freeze section 9 and SRS
4.5.3 freeze it. BundleLoader.load() reads it once and returns its records in memory:
bundle.json, the condition variants with their file hashes, the tasks, the manifest rows
with their generation metadata and output text, the execution records as read, and the
missing outputs. It stores nothing and writes nothing; storing the records is the RG10
unit's job, which is not built (Day 9 decision D9-9). Bundle code is handled only as
text (NFR-26): this module reads files and neither parses nor runs them.

A schema error raises BundleInvalid, whose exit code is 1 (FR-58), naming the file, the
row (the 1-based line of a .jsonl file) and the field. The checks run in a fixed order
and the first failure is reported. Messages name files, rows, fields and the bundle's own
IDs, never code, marker or output text.

Rules (Day 9 decision D9-7). [F] is stated by the frozen texts; [R] is a reading needed
to apply a frozen rule, provisional until confirmed; "not checked" means the frozen texts
give no basis, so nothing is enforced and the point is open for a decision.

* Files [F]: bundle.json, tasks.jsonl, manifest.jsonl, generation.jsonl, execution.jsonl
  as regular files; variants/, tests/ and outputs/ as directories. Every file is UTF-8;
  every .jsonl line is one JSON object; a final line break ends the last row [R]. JSON
  with a repeated key, NaN or Infinity is refused [R]. Keys not listed below are not
  checked and are not kept.
* No symbolic link, and no entry other than a regular file or a directory, in the parts
  read: the variants, the tasks' test folders and the output files [R; tree_sha256
  refuses symbolic links]. A file name that is not UTF-8 is refused [R]. In a variant or
  a task's test folder, a file or folder name holding a line break (U+000A) is refused (a
  new rule, Day 9 decision H1): tree_sha256 hashes "path TAB file-SHA-256" lines, which
  such a name makes ambiguous, so two different trees could get one hash. The message
  shows the path with the line break escaped. No other control character is refused.
* bundle.json [F]: schema_version, bundle_id, harness_version, prepare_module_version,
  env_fingerprint and created_at are required; prepare_module_version must equal
  PREPARE_MODULE_VERSION; env_fingerprint is an object whose "network" is "none" (the key
  name is [R]). The other five are non-empty strings [R]. The value of schema_version and
  the format of created_at are not checked.
* Variants [F]: each entry of variants/ is a smell folder; each entry of a smell folder is
  a variant folder named <condition> or <condition>-<dose>, the condition one of clean,
  placebo and injected and the dose non-empty; every smell has all three conditions. A
  variant has no .hidden_tests/ folder at its root [R; the executed tree puts the hidden
  tests there].
* tasks.jsonl [F]: task_id, project, target_module, marker, function_names, hidden_tests,
  project_tests and declared_opportunities are required. [R] task_id is unique and a
  plain name (it names tests/<task_id>/); project is a non-empty string; target_module
  is a relative POSIX path; marker is one non-empty line; function_names, hidden_tests
  and project_tests are lists of strings, test IDs unique within each list.
  declared_opportunities maps exactly the smell mapping's types [F] to integers >= 0
  [R]. tests/<task_id>/ is a directory [F].
* manifest.jsonl [F]: gen_id, prompt_file, output_file, task_id, smell, condition, dose,
  replicate and run_order are required; gen_id is unique; task_id names a task; the
  condition is one of the three. [R] dose is null or a non-empty string; replicate and
  run_order are integers; smell is one of the smell mapping's types (decision D9-7);
  (smell, condition, dose) names a variant folder. output_file is a relative POSIX path
  inside outputs/ [R, as the Day 3 harness stand-in reads it]; an absent file is a
  missing output, kept and listed, never dropped [F]; a present file must be UTF-8 [R].
  prompt_file is a non-empty string, recorded and never read [R; its location is not
  frozen].
* Markers [F]: a task's marker occurs exactly once in its target module in every variant
  that one of its manifest rows uses (counted as prepare_module counts it, with LF line
  endings); the target module must be a file of that variant and UTF-8 [R].
* Projects (Day 9 decision D6): a variant has one project (Freeze section 7 gives it one
  project attribute) [R], taken from the tasks whose manifest rows use it; a variant used
  by tasks of different projects is refused, naming both tasks (a new rule).
  variant_projects holds the project of each used variant. A variant no manifest row uses
  is validated like the others but has no project, so it is left out of variant_projects
  and is not to be stored.
* generation.jsonl [R]: each row's gen_id names a manifest row and is unique; the rest of
  the row is kept whole as the output's generation metadata (canonical JSON). The C9 key
  names are not frozen, so no C9 field is checked, and a manifest row without a
  generation row is not refused (blocked: Day 9 decision D9-7).
* execution.jsonl: each record's gen_id names a manifest row [F] and is unique [R]; every
  record's env_fingerprint equals bundle.json's, otherwise the bundle mixes environment
  fingerprints and is refused [F: FR-58, Decision 1 condition 4]. Every other field is
  checked per output by the ExecutionResultMatcher (FR-59), never here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tdea.analyzer.research.rg10.prepare import HIDDEN_TESTS, PREPARE_MODULE_VERSION, to_lf

DEFAULT_SMELL_MAPPING: dict[str, tuple[str, ...]] = {"T1": ("W0718", "W0702"), "T2": ("C0209",)}
CONDITIONS = ("clean", "placebo", "injected")
BUNDLE_JSON = "bundle.json"
TASKS = "tasks.jsonl"
MANIFEST = "manifest.jsonl"
GENERATION = "generation.jsonl"
EXECUTION = "execution.jsonl"
VARIANTS = "variants"
TESTS = "tests"
OUTPUTS = "outputs"
NETWORK = "network"
NETWORK_NONE = "none"
BUNDLE_TEXT_FIELDS = ("schema_version", "bundle_id", "harness_version",
                      "prepare_module_version", "created_at")

VariantKey = tuple[str, str, str | None]  # (smell, condition, dose)

_PLAIN_NAME = re.compile(r"[^/\\:\x00-\x1f]+")
LINE_BREAK = "\n"  # refused in hashed file and folder names (Day 9 decision H1)


class BundleInvalid(Exception):
    """A schema error in an RG10 bundle (FR-58): exit 1, naming the file, row and field."""

    exit_code = 1
    error_type = "RG10 bundle invalid"
    action = ("Correct the bundle in the research harness and load it again; the bundle "
              "was not evaluated and nothing was stored.")

    def __init__(self, file: str, row: int | None, field: str | None, reason: str) -> None:
        """Keep where the error is and why, and build the three FR-05 parts."""
        self.file = file
        self.row = row
        self.field = field
        self.reason = reason
        where = file + ("" if row is None else f" row {row}")
        where += "" if field is None else f", field {field}"
        self.cause = f"{where}: {reason}"
        super().__init__(f"{self.error_type}: {self.cause} {self.action}")


@dataclass(frozen=True)
class BundleMeta:
    """bundle.json; env_fingerprint is kept as canonical JSON."""

    schema_version: str
    bundle_id: str
    harness_version: str
    prepare_module_version: str
    env_fingerprint: str
    created_at: str


@dataclass(frozen=True)
class VariantDir:
    """One condition variant, variants/<smell>/<condition>[-<dose>]/, and its file hashes."""

    smell: str
    condition: str
    dose: str | None
    root: Path
    file_hashes: tuple[tuple[str, str], ...]  # (POSIX path in the variant, SHA-256), sorted

    @property
    def key(self) -> VariantKey:
        """Return (smell, condition, dose)."""
        return (self.smell, self.condition, self.dose)

    @property
    def relative(self) -> str:
        """Return the variant folder relative to the bundle, as a POSIX path."""
        name = self.condition if self.dose is None else f"{self.condition}-{self.dose}"
        return f"{VARIANTS}/{self.smell}/{name}"


@dataclass(frozen=True)
class TaskSpec:
    """One task of tasks.jsonl (row = its line) and its hidden-test folder."""

    row: int
    task_id: str
    project: str
    target_module: str
    marker: str
    function_names: tuple[str, ...]
    hidden_tests: tuple[str, ...]
    project_tests: tuple[str, ...]
    declared_opportunities: tuple[tuple[str, int], ...]  # (smell type, count), sorted
    hidden_tests_dir: Path


@dataclass(frozen=True)
class OutputSpec:
    """One manifest row (row = its line), its generation metadata and its output text."""

    row: int
    gen_id: str
    prompt_file: str
    output_file: str
    task_id: str
    smell: str
    condition: str
    dose: str | None
    replicate: int
    run_order: int
    generation_metadata: str | None  # canonical JSON of the generation row without gen_id
    text: str | None  # None when the output file is absent: a missing output

    @property
    def variant_key(self) -> VariantKey:
        """Return the (smell, condition, dose) of the output's variant."""
        return (self.smell, self.condition, self.dose)

    @property
    def missing(self) -> bool:
        """Return True for a missing output (FR-58)."""
        return self.text is None


@dataclass(frozen=True)
class LoadedBundle:
    """A validated bundle in memory (FR-58); outputs are ordered by gen_id."""

    root: Path
    meta: BundleMeta
    smell_mapping: tuple[tuple[str, tuple[str, ...]], ...]
    variants: dict[VariantKey, VariantDir]
    tasks: dict[str, TaskSpec]
    outputs: tuple[OutputSpec, ...]
    records: dict[str, dict[str, object]]  # execution records by gen_id, as read
    modules: dict[tuple[VariantKey, str], str]  # variant module text by (variant, path)
    variant_projects: dict[VariantKey, str]  # project of each used variant; unused: absent

    @property
    def missing_outputs(self) -> tuple[str, ...]:
        """Return the gen_ids of the missing outputs, in gen_id order (FR-58)."""
        return tuple(output.gen_id for output in self.outputs if output.missing)


class BundleLoader:
    """Reads and validates RG10 bundles under one smell mapping (FR-58)."""

    def __init__(self, smell_mapping: Mapping[str, Sequence[str]] | None = None) -> None:
        """Use ``smell_mapping``, or FR-61's default T1 = W0718 + W0702, T2 = C0209."""
        self._mapping = checked_mapping(DEFAULT_SMELL_MAPPING if smell_mapping is None
                                        else smell_mapping)

    def load(self, root: Path | str) -> LoadedBundle:
        """Return the bundle at ``root`` in memory, or raise BundleInvalid."""
        return _Reader(Path(root), self._mapping).read()


def checked_mapping(mapping: Mapping[str, Sequence[str]]) -> tuple[tuple[str, tuple[str, ...]],
                                                                   ...]:
    """Return the smell mapping as sorted (type, rules) pairs, or raise ValueError."""
    if not isinstance(mapping, Mapping) or not mapping:
        raise ValueError("the smell mapping must map at least one smell type to its rules")
    pairs = []
    for smell, rules in mapping.items():
        listed = isinstance(rules, Sequence) and not isinstance(rules, str) and bool(rules)
        if not (isinstance(smell, str) and smell and listed
                and all(isinstance(rule, str) and rule for rule in rules)):
            raise ValueError("each smell type must map to a non-empty list of rule IDs")
        pairs.append((smell, tuple(rules)))
    return tuple(sorted(pairs))


def canonical(value: object) -> str:
    """Return ``value`` as JSON with sorted keys and no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def shown(value: str) -> str:
    """Return a bundle ID for a message: quoted, escaped and at most 60 characters."""
    text = ascii(value)
    return text if len(text) <= 60 else text[:57] + "..."


def escaped(text: str) -> str:
    """Return a path for a message with control and non-ASCII characters escaped."""
    return text.encode("unicode_escape").decode("ascii")


def relative_path(value: str) -> PurePosixPath | None:
    """Return ``value`` as a relative POSIX path inside its folder, or None if it is not one."""
    if "\\" in value or ":" in value or any(ord(character) < 32 for character in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    return path


def _json(text: str) -> object:
    """Parse JSON, refusing a repeated key, NaN and Infinity."""
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant,
                      parse_float=_finite)


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object, refusing a repeated key."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("repeated key")
        result[key] = value
    return result


def _constant(name: str) -> float:
    """Refuse NaN and Infinity, which are not JSON."""
    raise ValueError(f"{name} is not JSON")


def _finite(text: str) -> float:
    """Parse a JSON number, refusing one too large to be finite."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("number out of range")
    return value


def _decoded(raw: bytes, file: str) -> str:
    """Decode a bundle file as UTF-8, or raise BundleInvalid."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BundleInvalid(file, None, None, "is not UTF-8 text") from None


def _mode(path: Path, where: str) -> int | None:
    """Return ``path``'s own mode (links not followed), or None if it does not exist."""
    try:
        return os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        raise BundleInvalid(where, None, None, "cannot be read") from None


def _raise(error: OSError) -> None:
    """Let os.walk report an unreadable folder instead of skipping it."""
    raise error


@dataclass(frozen=True)
class _Row:
    """One JSON object of a bundle file, with where it is and whose it is."""

    file: str
    number: int | None
    values: dict[str, object]
    owner: str

    def named(self, owner: str) -> _Row:
        """Return the same row, described as ``owner`` in messages."""
        return _Row(self.file, self.number, self.values, owner)

    def fail(self, field: str, reason: str) -> BundleInvalid:
        """Return the schema error for ``field``."""
        return BundleInvalid(self.file, self.number, field, f"{self.owner} {reason}")

    def get(self, field: str) -> object:
        """Return a required field's value."""
        if field not in self.values:
            raise self.fail(field, f"has no {field}")
        return self.values[field]

    def text(self, field: str) -> str:
        """Return a required non-empty string."""
        value = self.get(field)
        if not isinstance(value, str) or not value:
            raise self.fail(field, f"needs a non-empty string as {field}")
        return value

    def name(self, field: str) -> str:
        """Return a required plain name: no path separator, colon or control character."""
        value = self.text(field)
        if not _PLAIN_NAME.fullmatch(value) or value in (".", ".."):
            raise self.fail(field, f"needs a plain name as {field}")
        return value

    def optional_name(self, field: str) -> str | None:
        """Return a required field that is null or a plain name."""
        return None if self.get(field) is None else self.name(field)

    def path(self, field: str) -> PurePosixPath:
        """Return a required relative POSIX path that stays inside its folder."""
        path = relative_path(self.text(field))
        if path is None:
            raise self.fail(field, f"needs a relative POSIX path as {field}")
        return path

    def integer(self, field: str) -> int:
        """Return a required integer; JSON true and false do not count as integers."""
        value = self.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise self.fail(field, f"needs an integer as {field}")
        return value

    def texts(self, field: str, unique: bool = False) -> tuple[str, ...]:
        """Return a required list of strings, without repeats when ``unique``."""
        value = self.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise self.fail(field, f"needs a list of strings as {field}")
        if unique and len(set(value)) != len(value):
            raise self.fail(field, f"repeats an entry of {field}")
        return tuple(value)


class _Reader:
    """Reads one bundle, check by check, in the fixed order of read()."""

    def __init__(self, root: Path, mapping: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
        """Read the bundle at ``root`` under the checked smell mapping."""
        self._root = root
        self._mapping = mapping
        self._types = tuple(smell for smell, _rules in mapping)

    def read(self) -> LoadedBundle:
        """Run every check and return the bundle."""
        if not self._root.is_dir():
            raise BundleInvalid(str(self._root), None, None, "is not a bundle folder")
        meta = self._meta()
        variants = self._variants()
        tasks = self._tasks()
        manifest = self._manifest(tasks, variants)
        modules = self._modules(manifest, tasks, variants)
        projects = self._projects(manifest, tasks, variants)
        generation = self._generation(manifest)
        records = self._execution(manifest, meta)
        self._folder(OUTPUTS)
        outputs = tuple(self._output(row, generation.get(row.text("gen_id")))
                        for row in sorted(manifest.values(), key=lambda row: row.text("gen_id")))
        return LoadedBundle(self._root, meta, self._mapping, variants, tasks, outputs,
                            records, modules, projects)

    # --- files ----------------------------------------------------------------------

    def _file(self, name: str) -> bytes:
        """Return the bytes of a required regular file of the bundle."""
        mode = _mode(self._root / name, name)
        if mode is None:
            raise BundleInvalid(name, None, None, "is missing")
        if not stat.S_ISREG(mode):
            raise BundleInvalid(name, None, None, "is not a regular file")
        try:
            return (self._root / name).read_bytes()
        except OSError:
            raise BundleInvalid(name, None, None, "cannot be read") from None

    def _folder(self, relative: str) -> Path:
        """Return a required folder of the bundle (a real folder, not a link)."""
        path = self._root.joinpath(*PurePosixPath(relative).parts)
        mode = _mode(path, relative)
        if mode is None:
            raise BundleInvalid(relative, None, None, "is missing")
        if not stat.S_ISDIR(mode):
            raise BundleInvalid(relative, None, None, "is not a folder")
        return path

    def _subfolders(self, relative: str) -> list[str]:
        """Return the sorted entries of a folder, each of which must be a folder."""
        path = self._folder(relative)
        try:
            names = sorted(os.listdir(path))
        except OSError:
            raise BundleInvalid(relative, None, None, "cannot be read") from None
        for name in names:
            self._folder(f"{relative}/{name}")
        return names

    def _rows(self, name: str) -> list[_Row]:
        """Return the JSON objects of a .jsonl file, one per line."""
        lines = _decoded(self._file(name), name).split("\n")
        if lines[-1] == "":
            lines.pop()
        return [_Row(name, number, self._object(line, name, number), "the row")
                for number, line in enumerate(lines, 1)]

    @staticmethod
    def _object(text: str, file: str, number: int | None) -> dict[str, object]:
        """Return the JSON object in ``text``, or raise BundleInvalid."""
        try:
            value = _json(text)
        except (ValueError, RecursionError):
            raise BundleInvalid(file, number, None, "is not valid JSON") from None
        if not isinstance(value, dict):
            raise BundleInvalid(file, number, None, "is not a JSON object")
        return value

    def _tree(self, relative: str) -> tuple[tuple[str, str], ...]:
        """Return (path, SHA-256) of every file under a folder; refuse links and the like."""
        root = self._folder(relative)
        hashes = []
        try:
            for directory, subfolders, names in os.walk(root, onerror=_raise):
                subfolders.sort()
                for name in sorted(subfolders + names):
                    entry = Path(directory, name)
                    inside = entry.relative_to(root).as_posix()
                    self._regular_or_folder(entry, f"{relative}/{inside}")
                    if name in names:
                        hashes.append((inside, hashlib.sha256(entry.read_bytes()).hexdigest()))
        except OSError:
            raise BundleInvalid(relative, None, None, "cannot be read") from None
        return tuple(sorted(hashes))

    @staticmethod
    def _regular_or_folder(entry: Path, where: str) -> None:
        """Refuse a symbolic link, a special file, a name that is not UTF-8 or a line break."""
        try:
            where.encode("utf-8")
        except UnicodeEncodeError:
            raise BundleInvalid(where.encode("utf-8", "replace").decode("utf-8"), None, None,
                                "has a file name that is not UTF-8") from None
        if LINE_BREAK in entry.name:
            raise BundleInvalid(escaped(where), None, None,
                                "has a line break (U+000A) in its name, which would make the "
                                "executed tree's hash ambiguous")
        mode = os.lstat(entry).st_mode
        if stat.S_ISLNK(mode):
            raise BundleInvalid(where, None, None, "is a symbolic link, which a bundle may "
                                "not contain")
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BundleInvalid(where, None, None, "is neither a regular file nor a folder")

    # --- bundle.json and variants ---------------------------------------------------

    def _meta(self) -> BundleMeta:
        """Check bundle.json."""
        row = _Row(BUNDLE_JSON, None, self._object(_decoded(self._file(BUNDLE_JSON),
                                                            BUNDLE_JSON), BUNDLE_JSON, None),
                   "bundle.json")
        texts = {field: row.text(field) for field in BUNDLE_TEXT_FIELDS}
        version = texts["prepare_module_version"]
        if version != PREPARE_MODULE_VERSION:
            raise row.fail("prepare_module_version",
                           f"has prepare_module_version {shown(version)}; TDEA's is "
                           f"{shown(PREPARE_MODULE_VERSION)}")
        fingerprint = row.get("env_fingerprint")
        if not isinstance(fingerprint, dict):
            raise row.fail("env_fingerprint", "needs an object as env_fingerprint")
        if fingerprint.get(NETWORK) != NETWORK_NONE:
            raise row.fail("env_fingerprint", 'needs network "none" in env_fingerprint')
        return BundleMeta(texts["schema_version"], texts["bundle_id"], texts["harness_version"],
                          version, canonical(fingerprint), texts["created_at"])

    def _variants(self) -> dict[VariantKey, VariantDir]:
        """Check variants/<smell>/<condition>[-<dose>]/ and hash each variant's files."""
        variants: dict[VariantKey, VariantDir] = {}
        for smell in self._subfolders(VARIANTS):
            found = set()
            for name in self._subfolders(f"{VARIANTS}/{smell}"):
                condition, separator, dose = name.partition("-")
                if condition not in CONDITIONS or (separator and not dose):
                    raise BundleInvalid(f"{VARIANTS}/{smell}/{name}", None, None,
                                        "is not named <condition> or <condition>-<dose> with "
                                        "a condition of clean, placebo or injected")
                relative = f"{VARIANTS}/{smell}/{name}"
                hashes = self._tree(relative)
                if any(path.split("/")[0] == HIDDEN_TESTS for path, _digest in hashes):
                    raise BundleInvalid(f"{relative}/{HIDDEN_TESTS}", None, None,
                                        "is reserved for the executed tree's hidden tests")
                found.add(condition)
                variant = VariantDir(smell, condition, dose if separator else None,
                                     self._folder(relative), hashes)
                variants[variant.key] = variant
            for condition in CONDITIONS:
                if condition not in found:
                    raise BundleInvalid(f"{VARIANTS}/{smell}", None, None,
                                        f"has no {condition} variant")
        return variants

    # --- tasks and manifest ---------------------------------------------------------

    def _tasks(self) -> dict[str, TaskSpec]:
        """Check tasks.jsonl and every task's tests/<task_id>/ folder."""
        self._folder(TESTS)
        tasks: dict[str, TaskSpec] = {}
        for plain in self._rows(TASKS):
            task_id = plain.name("task_id")
            row = plain.named(f"task {shown(task_id)}")
            if task_id in tasks:
                raise row.fail("task_id", f"repeats task_id from row {tasks[task_id].row}")
            fields = (row.text("project"), row.path("target_module").as_posix(),
                      self._marker(row), row.texts("function_names"),
                      row.texts("hidden_tests", unique=True),
                      row.texts("project_tests", unique=True))
            declared = self._declared(row)
            self._tree(f"{TESTS}/{task_id}")
            tasks[task_id] = TaskSpec(plain.number or 0, task_id, *fields, declared,
                                      self._folder(f"{TESTS}/{task_id}"))
        return tasks

    @staticmethod
    def _marker(row: _Row) -> str:
        """Return the task marker: one non-empty line."""
        marker = row.text("marker")
        if "\n" in to_lf(marker):
            raise row.fail("marker", "needs a single line as marker")
        return marker

    def _declared(self, row: _Row) -> tuple[tuple[str, int], ...]:
        """Return the declared opportunities: exactly the smell mapping's types, counts >= 0."""
        value = row.get("declared_opportunities")
        if not isinstance(value, dict):
            raise row.fail("declared_opportunities", "needs an object as declared_opportunities")
        for smell in sorted(value):
            if smell not in self._types:
                raise row.fail("declared_opportunities",
                               f"declares type {shown(smell)}, which the smell mapping lacks")
        for smell in self._types:
            if smell not in value:
                raise row.fail("declared_opportunities",
                               f"declares no opportunities for type {shown(smell)}")
        for smell in self._types:
            count = value[smell]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise row.fail("declared_opportunities",
                               f"needs an integer of at least 0 for type {shown(smell)}")
        return tuple((smell, value[smell]) for smell in self._types)

    def _manifest(self, tasks: dict[str, TaskSpec],
                  variants: dict[VariantKey, VariantDir]) -> dict[str, _Row]:
        """Check manifest.jsonl; return its rows by gen_id."""
        rows: dict[str, _Row] = {}
        for plain in self._rows(MANIFEST):
            gen_id = plain.text("gen_id")
            row = plain.named(f"output {shown(gen_id)}")
            if gen_id in rows:
                raise row.fail("gen_id", f"repeats gen_id from row {rows[gen_id].number}")
            row.text("prompt_file")
            row.path("output_file")
            if row.text("task_id") not in tasks:
                raise row.fail("task_id", f"names unknown task {shown(row.text('task_id'))}")
            smell = row.text("smell")
            if smell not in self._types:
                raise row.fail("smell", f"names smell {shown(smell)}, which the smell mapping "
                               "lacks")
            condition = row.text("condition")
            if condition not in CONDITIONS:
                raise row.fail("condition", "needs clean, placebo or injected as condition")
            key = (smell, condition, row.optional_name("dose"))
            row.integer("replicate")
            row.integer("run_order")
            if key not in variants:
                folder = condition if key[2] is None else f"{condition}-{key[2]}"
                raise row.fail("condition", f"uses variant {VARIANTS}/{smell}/{folder}, which "
                               "the bundle lacks")
            rows[gen_id] = row
        return rows

    def _modules(self, manifest: dict[str, _Row], tasks: dict[str, TaskSpec],
                 variants: dict[VariantKey, VariantDir]) -> dict[tuple[VariantKey, str], str]:
        """Check each used (task, variant) pair's marker; return the variant modules read."""
        modules: dict[tuple[VariantKey, str], str] = {}
        checked = set()
        for gen_id in sorted(manifest):
            row = manifest[gen_id]
            task = tasks[row.text("task_id")]
            variant = variants[(row.text("smell"), row.text("condition"),
                                row.optional_name("dose"))]
            if (task.task_id, variant.key) in checked:
                continue
            checked.add((task.task_id, variant.key))
            key = (variant.key, task.target_module)
            if key not in modules:
                modules[key] = self._module(task, variant)
            count = to_lf(modules[key]).count(task.marker)
            if count != 1:
                raise BundleInvalid(TASKS, task.row, "marker",
                                    f"task {shown(task.task_id)} has a marker that occurs "
                                    f"{count} times in {variant.relative}/{task.target_module}; "
                                    "it must occur exactly once")
        return modules

    @staticmethod
    def _projects(manifest: dict[str, _Row], tasks: dict[str, TaskSpec],
                  variants: dict[VariantKey, VariantDir]) -> dict[VariantKey, str]:
        """Return each used variant's project; refuse one shared by different projects."""
        users: dict[VariantKey, TaskSpec] = {}
        for gen_id in sorted(manifest):
            row = manifest[gen_id]
            task = tasks[row.text("task_id")]
            variant = variants[(row.text("smell"), row.text("condition"),
                                row.optional_name("dose"))]
            first = users.setdefault(variant.key, task)
            if first.project != task.project:
                raise BundleInvalid(TASKS, task.row, "project",
                                    f"task {shown(task.task_id)} has project "
                                    f"{shown(task.project)} but shares {variant.relative} "
                                    f"with task {shown(first.task_id)} of project "
                                    f"{shown(first.project)}; a variant tree belongs to one "
                                    "project")
        return {key: task.project for key, task in users.items()}

    def _module(self, task: TaskSpec, variant: VariantDir) -> str:
        """Return the text of the task's target module in ``variant``."""
        where = f"{variant.relative}/{task.target_module}"
        if task.target_module not in dict(variant.file_hashes):
            raise BundleInvalid(TASKS, task.row, "target_module",
                                f"task {shown(task.task_id)} targets {where}, which is not a "
                                "file of the variant")
        path = variant.root.joinpath(*PurePosixPath(task.target_module).parts)
        try:
            raw = path.read_bytes()
        except OSError:
            raise BundleInvalid(where, None, None, "cannot be read") from None
        return _decoded(raw, where)

    # --- generation metadata, execution records and outputs ------------------------

    def _generation(self, manifest: dict[str, _Row]) -> dict[str, str]:
        """Check generation.jsonl; return each output's metadata as canonical JSON."""
        metadata: dict[str, str] = {}
        rows: dict[str, int | None] = {}
        for plain in self._rows(GENERATION):
            gen_id = plain.text("gen_id")
            row = plain.named(f"generation row for {shown(gen_id)}")
            if gen_id not in manifest:
                raise row.fail("gen_id", "matches no manifest row")
            if gen_id in rows:
                raise row.fail("gen_id", f"repeats gen_id from row {rows[gen_id]}")
            rows[gen_id] = row.number
            metadata[gen_id] = canonical({key: value for key, value in row.values.items()
                                          if key != "gen_id"})
        return metadata

    def _execution(self, manifest: dict[str, _Row],
                   meta: BundleMeta) -> dict[str, dict[str, object]]:
        """Check execution.jsonl at the bundle level; return the records by gen_id."""
        records: dict[str, dict[str, object]] = {}
        rows: dict[str, int | None] = {}
        for plain in self._rows(EXECUTION):
            gen_id = plain.text("gen_id")
            row = plain.named(f"execution record for {shown(gen_id)}")
            if gen_id not in manifest:
                raise row.fail("gen_id", "matches no manifest row")
            if gen_id in rows:
                raise row.fail("gen_id", f"repeats gen_id from row {rows[gen_id]}")
            fingerprint = row.get("env_fingerprint")
            if not isinstance(fingerprint, dict) or canonical(fingerprint) != meta.env_fingerprint:
                raise row.fail("env_fingerprint", "has an env_fingerprint that differs from "
                               "bundle.json's, so the bundle mixes environment fingerprints")
            rows[gen_id] = row.number
            records[gen_id] = row.values
        return records

    def _output(self, row: _Row, metadata: str | None) -> OutputSpec:
        """Return one output: its manifest fields, metadata and text (None if missing)."""
        name = row.path("output_file")
        where = f"{OUTPUTS}/{name.as_posix()}"
        path = self._root.joinpath(OUTPUTS, *name.parts)
        mode = _mode(path, where)
        text = None
        if mode is not None:
            if not stat.S_ISREG(mode):
                raise BundleInvalid(where, None, None, "is not a regular file")
            try:
                raw = path.read_bytes()
            except OSError:
                raise BundleInvalid(where, None, None, "cannot be read") from None
            text = _decoded(raw, where)
        return OutputSpec(row.number or 0, row.text("gen_id"), row.text("prompt_file"),
                          name.as_posix(), row.text("task_id"), row.text("smell"),
                          row.text("condition"), row.optional_name("dose"),
                          row.integer("replicate"), row.integer("run_order"), metadata, text)


def bundle_content_hash(root: Path | str) -> str:
    """Return the bundle content hash that identifies an RG10 bundle (FR-48, run identifier).

    The frozen texts name the hash but do not define it (sprint 23 Oct, provisional):
    SHA-256 over the sorted UTF-8 lines "path TAB file SHA-256", one per regular file in
    the bundle folder, with POSIX paths relative to it, as tree_sha256 hashes a tree. The
    folder's own name and location, and file times, do not count, so a copied bundle keeps
    its hash. A symbolic link or a file name holding a line break is refused, since either
    would make the listing ambiguous.
    """
    root = Path(root)
    if not root.is_dir():
        raise BundleInvalid(str(root), None, None, "is not a bundle folder")
    lines = []
    for directory, folders, names in os.walk(root, onerror=_raise):
        folders.sort()
        for name in folders + names:
            path = Path(directory, name)
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or LINE_BREAK in name:
                raise BundleInvalid(escaped(relative), None, None,
                                    "cannot be hashed: symbolic links and names with a line "
                                    "break are not allowed in a bundle")
        for name in names:
            path = Path(directory, name)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{path.relative_to(root).as_posix()}\t{digest}")
    listing = "".join(f"{line}\n" for line in sorted(lines))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()
