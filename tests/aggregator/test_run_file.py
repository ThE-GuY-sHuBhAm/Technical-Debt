"""FR-48 run-file validation: every listed rule, the readings, and the blocked keys.

Run files are written as text and validated through ResearchService with the real
ResearchEngine (FR-02 is a pattern check, so nothing reaches the network) and a fixed
clock. The repository URL is the placeholder of the SRS 4.5.2 schema. These tests need no
Pylint, so they run in every CI cell.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from run_files import schema_example

from tdea.aggregator.research_service import ResearchService
from tdea.aggregator.run_file import INVALID, UNSUPPORTED, Rg10RunFile, RunFileInvalid
from tdea.analyzer.research.engine import ResearchEngine, UrlRejected

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40
SHA_B = "b" * 40
VALID = schema_example(commits=(SHA_A, SHA_B))


def check(tmp_path: Path, text: str | bytes) -> object:
    """Validate ``text`` as a run file and return the result."""
    path = tmp_path / "run.yaml"
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return ResearchService(clock=lambda: NOW).validate(path)


def problems(tmp_path: Path, text: str | bytes) -> list:
    """Return the problems validation reports for ``text``."""
    with pytest.raises(RunFileInvalid) as caught:
        check(tmp_path, text)
    return list(caught.value.problems)


def causes(tmp_path: Path, text: str | bytes) -> list[str]:
    """Return the causes of the reported problems."""
    return [problem.cause for problem in problems(tmp_path, text)]


def test_the_schema_example_is_valid(tmp_path: Path) -> None:
    """The SRS 4.5.2 example validates; windows are UTC and units = repositories x windows."""
    run = check(tmp_path, VALID)
    assert (run.kind, run.store, run.w_months, run.tau, run.cutoff) == (
        "rg1", "research.db", 6, 0.7, None)
    assert run.seeds == {"sampling": 20261005} and run.unit_count == 2
    repository = run.repositories[0]
    assert (repository.owner, repository.name, repository.head) == ("owner", "name", None)
    assert repository.windows[0].start == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert repository.windows[1].commits == (SHA_A, SHA_B)


def test_missing_w_is_rejected_by_name(tmp_path: Path) -> None:
    """A run file without W is invalid input (FR-48, RE-01 step 3)."""
    assert causes(tmp_path, VALID.replace("w_months: 6\n", "")) == [
        "W is missing: the key 'w_months' is required."]


@pytest.mark.parametrize("value", ["0", "-1", "6.0", "true", "'6'"])
def test_w_must_be_a_positive_whole_number(tmp_path: Path, value: str) -> None:
    """W is a whole number of calendar months, 1 or more."""
    assert causes(tmp_path, VALID.replace("w_months: 6", f"w_months: {value}")) == [
        "w_months must be a whole number of months, 1 or more."]


@pytest.mark.parametrize("value", ["0.5", "0.6", "0.7", "0.8"])
def test_the_four_stored_tau_values_are_accepted(tmp_path: Path, value: str) -> None:
    """tau may be any of the four values whose modes are stored (FR-56)."""
    assert check(tmp_path, VALID.replace("tau: 0.7", f"tau: {value}")).tau == float(value)


@pytest.mark.parametrize("value", ["0.65", "1", "0.9", "true", "'0.7'"])
def test_any_other_tau_is_rejected(tmp_path: Path, value: str) -> None:
    """A tau other than 0.5, 0.6, 0.7 or 0.8 is invalid (FR-48)."""
    assert causes(tmp_path, VALID.replace("tau: 0.7", f"tau: {value}")) == [
        "tau must be 0.5, 0.6, 0.7 or 0.8."]


@pytest.mark.parametrize("key", ["store", "w_months", "repositories"])
def test_a_required_key_set_to_null_counts_as_missing(tmp_path: Path, key: str) -> None:
    """kind: rg1 with ``tau: null`` and the like is a missing key, never a silent default."""
    lines = [f"{key}: null\n" if line.startswith(f"{key}:") else line
             for line in VALID.splitlines(keepends=True)]
    if key == "repositories":
        lines = lines[:lines.index("repositories: null\n") + 1]
    reported = causes(tmp_path, "".join(lines))
    assert reported == (["W is missing: the key 'w_months' is required."] if key == "w_months"
                        else [f"The required key '{key}' is missing."])


@pytest.mark.parametrize("bounds", ["{start: 2025-07-01, end: 2025-07-01}",
                                    "{start: 2025-08-01, end: 2025-07-01}"])
def test_a_window_must_start_before_it_ends(tmp_path: Path, bounds: str) -> None:
    """start >= end is rejected (FR-48)."""
    text = VALID.replace("{start: 2025-01-01, end: 2025-07-01}", bounds)
    assert causes(tmp_path, text) == [
        "Repository 1: window 1: the start is not before the end."]


def test_overlapping_windows_are_rejected_and_adjacent_ones_accepted(tmp_path: Path) -> None:
    """Windows of one repository must not overlap; one may start where another ends."""
    assert check(tmp_path, VALID).unit_count == 2  # 2025-07-01 is shared as end and start
    text = VALID.replace("end: 2025-07-01}", "end: 2025-07-02}")
    assert causes(tmp_path, text) == ["Repository 1: windows 1 and 2 overlap."]


def test_a_cutoff_in_the_future_is_rejected(tmp_path: Path) -> None:
    """The cut-off may be null or past; a future cut-off is invalid (FR-48)."""
    past = check(tmp_path, VALID.replace("cutoff: null", "cutoff: 2026-10-01T00:00:00Z"))
    assert past.cutoff == datetime(2026, 10, 1, tzinfo=timezone.utc)
    future = VALID.replace("cutoff: null", "cutoff: 2026-10-12T12:00:01Z")
    assert causes(tmp_path, future) == ["The cut-off lies in the future."]


@pytest.mark.parametrize("url", ["https://gitlab.com/owner/name",
                                 "https://github.com/owner/name/tree/main",
                                 "github.com/owner/name", "''"])
def test_a_url_failing_fr02_is_rejected_with_its_reason(tmp_path: Path, url: str) -> None:
    """The FR-02 check runs on every repository URL and reports the validator's cause."""
    reported = problems(tmp_path, VALID.replace("https://github.com/owner/name", url))
    assert [problem.error_type for problem in reported] == ["Invalid repository URL"]
    assert reported[0].cause.startswith("Repository 1: ")


def test_unknown_keys_are_rejected_at_every_level(tmp_path: Path) -> None:
    """Unknown keys are invalid in the run file, a repository and a window (FR-48)."""
    text = (VALID.replace("tau: 0.7", "tau: 0.7\nthreads: 4")
            .replace("    head: null", "    head: null\n    branch: main")
            .replace("end: 2025-07-01}", "end: 2025-07-01, label: first}"))
    assert causes(tmp_path, text) == [
        "The key 'threads' is not part of the RG1 run-file schema.",
        "Repository 1: The key 'branch' is not part of the RG1 run-file schema.",
        "Repository 1: window 1: The key 'label' is not part of the RG1 run-file schema.",
    ]


@pytest.mark.parametrize("key", ["store", "repositories"])
def test_required_keys_must_be_present(tmp_path: Path, key: str) -> None:
    """Keys the schema shows without an optional note are required (Task 7 reading)."""
    lines = VALID.splitlines(keepends=True)
    start = next(index for index, line in enumerate(lines) if line.startswith(f"{key}:"))
    end = start + 1
    while end < len(lines) and lines[end].startswith(" "):
        end += 1
    assert f"The required key '{key}' is missing." in causes(
        tmp_path, "".join(lines[:start] + lines[end:]))


def test_optional_keys_may_be_left_out(tmp_path: Path) -> None:
    """key_file, lint_cache, cutoff, head and a window's commits are optional."""
    text = (VALID.replace("key_file: ~/.tdea/research.key\n", "")
            .replace("cutoff: null\n", "").replace("    head: null\n", "")
            .replace(f", commits: [{SHA_A}, {SHA_B}]", ""))
    run = check(tmp_path, text)
    assert run.key_file is None and run.repositories[0].windows[1].commits is None


@pytest.mark.parametrize("head", ["abc123", "A" * 40, "'" + "a" * 39 + "'"])
def test_head_must_be_a_full_lowercase_sha(tmp_path: Path, head: str) -> None:
    """A pinned HEAD is a full 40-character lowercase SHA (as ResearchCommit records it)."""
    reported = causes(tmp_path, VALID.replace("head: null", f"head: {head}"))
    assert reported == ["Repository 1: head must be a full 40-character commit SHA in "
                        "lowercase hex, or null."]
    assert check(tmp_path, VALID.replace("head: null", f"head: {SHA_A}")) \
        .repositories[0].head == SHA_A


@pytest.mark.parametrize("commits, cause", [
    ("[]", "commits must be a non-empty list"), ("[abc123]", "commits must be a non-empty"),
    (f"[{SHA_A}, {SHA_A}]", "commits lists a SHA more than once.")])
def test_a_sha_list_must_hold_distinct_full_shas(tmp_path: Path, commits: str,
                                                  cause: str) -> None:
    """A window's SHA list is a non-empty list of distinct full SHAs."""
    text = VALID.replace(f"[{SHA_A}, {SHA_B}]", commits)
    assert cause in causes(tmp_path, text)[0]


def test_a_repository_listed_twice_is_rejected(tmp_path: Path) -> None:
    """Two entries naming one repository (case aside) are ambiguous."""
    second = ("  - url: https://github.com/Owner/Name\n"
              "    windows:\n      - {start: 2024-01-01, end: 2024-07-01}\n")
    assert causes(tmp_path, VALID + second) == [
        "Repository 2 names the same repository as repository 1."]


@pytest.mark.parametrize("seeds", ["20261005", "{sampling: x}", "{sampling: true}"])
def test_seeds_map_names_to_whole_numbers(tmp_path: Path, seeds: str) -> None:
    """Seeds are recorded only; they must map names to whole numbers."""
    text = VALID.replace("{sampling: 20261005}", seeds)
    assert causes(tmp_path, text) == ["seeds must map names to whole numbers."]


def test_dates_and_timestamps_are_read_in_utc(tmp_path: Path) -> None:
    """Quoted dates, offsets and naive timestamps all become UTC bounds (Task 7 reading)."""
    text = VALID.replace("{start: 2025-01-01, end: 2025-07-01}",
                         "{start: '2025-01-01', end: 2025-07-01T05:30:00+05:30}") \
        .replace("{start: 2025-07-01, end: 2026-01-01,",
                 "{start: 2025-07-01T00:00:00, end: 2026-01-01,")
    windows = check(tmp_path, text).repositories[0].windows
    assert windows[0].start == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert windows[0].end == datetime(2025, 7, 1, tzinfo=timezone.utc)
    assert windows[1].start == datetime(2025, 7, 1, tzinfo=timezone.utc)


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    """Several faults give several three-part problems, in a fixed order."""
    text = (VALID.replace("w_months: 6\n", "").replace("tau: 0.7", "tau: 0.9")
            .replace("end: 2025-07-01}", "end: 2025-08-01}"))
    reported = problems(tmp_path, text)
    assert [problem.cause for problem in reported] == [
        "W is missing: the key 'w_months' is required.",
        "tau must be 0.5, 0.6, 0.7 or 0.8.",
        "Repository 1: windows 1 and 2 overlap.",
    ]
    assert all(problem.error_type and problem.action for problem in reported)


def test_a_token_anywhere_rejects_the_file_without_echoing_it(
    tmp_path: Path, canary_token: str
) -> None:
    """A GitHub token in a value, a key or a comment is refused and never repeated."""
    for text in (VALID + f"# {canary_token}\n",
                 VALID.replace("research.db", canary_token),
                 VALID + f"{canary_token}: 1\n"):
        reported = problems(tmp_path, text)
        assert [problem.error_type for problem in reported] == ["Run file contains a token"]
        assert canary_token not in repr(reported)


def test_lint_cache_is_an_optional_path(tmp_path: Path) -> None:
    """Freeze rev. 116 FR-48 schema: lint_cache is an optional path; null means the default."""
    assert check(tmp_path, VALID).lint_cache is None
    run = check(tmp_path, VALID + "lint_cache: ~/.tdea/lintcache.db\n")
    assert run.lint_cache == "~/.tdea/lintcache.db"
    assert check(tmp_path, VALID + "lint_cache: null\n").lint_cache is None


def test_a_lint_cache_of_the_wrong_shape_is_rejected(tmp_path: Path) -> None:
    """lint_cache, like key_file, must be a file path or null."""
    assert causes(tmp_path, VALID + "lint_cache: 5\n") == [
        "lint_cache must be a file path or null."]


RG10 = "kind: rg10\nstore: research.db\nbundle: bundles/rg10-01\n"


def test_an_rg10_run_file_takes_the_default_smell_mapping(tmp_path: Path) -> None:
    """RG10 keys (sprint 23 Oct reading of CL-05/CL-06): kind, store, bundle; mapping optional."""
    run_file = check(tmp_path, RG10)
    assert isinstance(run_file, Rg10RunFile)
    assert (run_file.store, run_file.bundle, run_file.unit_count) == (
        "research.db", "bundles/rg10-01", 1)
    assert run_file.smell_mapping == (("T1", ("W0718", "W0702")), ("T2", ("C0209",)))


def test_an_rg10_run_file_may_name_its_smell_mapping(tmp_path: Path) -> None:
    """A smell mapping in the run file replaces the default, sorted by smell type."""
    run_file = check(tmp_path, RG10 + "smell_mapping: {T2: [C0209], T1: [W0702]}\n")
    assert run_file.smell_mapping == (("T1", ("W0702",)), ("T2", ("C0209",)))


@pytest.mark.parametrize("extra", ["key_file: k.key\n", "lint_cache: c.db\n", "seeds: {s: 1}\n",
                                   "w_months: 6\n"])
def test_rg1_keys_are_not_rg10_keys(tmp_path: Path, extra: str) -> None:
    """CL-05 is open, so no key beyond the frozen RG10 schema is accepted."""
    (cause,) = causes(tmp_path, RG10 + extra)
    assert "is not part of the RG10 run-file schema" in cause


@pytest.mark.parametrize("mapping", ["[W0718]", "{T1: []}", "{T1: W0718}", "{}", "{T1: [1]}"])
def test_a_malformed_smell_mapping_is_rejected(tmp_path: Path, mapping: str) -> None:
    """smell_mapping must map each smell type to a non-empty list of message IDs."""
    (cause,) = causes(tmp_path, RG10 + f"smell_mapping: {mapping}\n")
    assert cause.startswith("smell_mapping must map each smell type")


@pytest.mark.parametrize("key", ["store", "bundle"])
def test_rg10_required_keys_must_be_present(tmp_path: Path, key: str) -> None:
    """store and bundle are required; the bundle itself is checked when the run starts."""
    text = "".join(line + "\n" for line in RG10.splitlines() if not line.startswith(key))
    assert any(key in cause for cause in causes(tmp_path, text))


@pytest.mark.parametrize("text, cause", [
    ("- kind: rg1\n", "The run file must be a YAML mapping of keys to values."),
    ("store: research.db\n", "The required key 'kind' is missing."),
    ("kind: rg2\n", "kind must be rg1 or rg10."),
    ("kind: rg1\nkind: rg1\n", "The key 'kind' appears twice in one mapping (line 2)."),
    ("kind: [rg1\n", "The YAML parser stopped at line 2."),
    (b"kind: rg1\nstore: caf\xe9\n", "The file is not UTF-8 text."),
])
def test_unreadable_or_malformed_files_are_rejected(tmp_path: Path, text: str | bytes,
                                                     cause: str) -> None:
    """Shape, kind, duplicate keys, YAML syntax and encoding are checked first."""
    assert causes(tmp_path, text) == [cause]


def test_a_missing_file_and_a_bom_file(tmp_path: Path) -> None:
    """A missing file is invalid input; a UTF-8 BOM is accepted."""
    with pytest.raises(RunFileInvalid) as caught:
        ResearchService(clock=lambda: NOW).validate(tmp_path / "absent.yaml")
    assert caught.value.error_type == "Run file cannot be read"
    assert check(tmp_path, b"\xef\xbb\xbf" + VALID.encode()).kind == "rg1"


def test_problems_have_three_non_empty_parts(tmp_path: Path) -> None:
    """Every problem states what happened, why and what to do (FR-05, NFR-11)."""
    text = "kind: rg1\nrepositories: [{url: x, windows: [{start: 1, end: 2}]}]\n"
    for problem in problems(tmp_path, text):
        assert problem.error_type in (INVALID, UNSUPPORTED, "Invalid repository URL")
        assert problem.cause.strip() and problem.action.strip()


@pytest.mark.parametrize("key, start", [("tau", "tau is missing."),
                                        ("seeds", "seeds is missing.")])
@pytest.mark.parametrize("absent", [True, False])
def test_a_missing_tau_or_seeds_is_blocked_not_guessed(tmp_path: Path, key: str, start: str,
                                                       absent: bool) -> None:
    """tau and seeds: the schema shows no optional note, but the SRS gives tau a default
    and only records seeds; refused as not yet settled, whether absent or null."""
    lines = [line for line in VALID.splitlines(keepends=True) if not line.startswith(f"{key}:")]
    if not absent:
        lines.insert(1, f"{key}: null\n")
    reported = problems(tmp_path, "".join(lines))
    assert [(p.error_type, p.blocked) for p in reported] == [(UNSUPPORTED, True)]
    assert reported[0].cause.startswith(start)


@pytest.mark.parametrize("value", ["2025-02-30", "2025-13-01", "!!int abc", "!!float abc",
                                   "!!timestamp nope", "2025-01-01 00:00:00 +99:00"])
def test_values_yaml_cannot_build_are_three_part_errors(tmp_path: Path, value: str) -> None:
    """An impossible date or a bad tag is reported as invalid YAML, never raised raw."""
    reported = problems(tmp_path, VALID.replace("cutoff: null", f"cutoff: {value}"))
    assert [p.error_type for p in reported] == ["Run file is not valid YAML"]


def test_extreme_nesting_is_a_three_part_error(tmp_path: Path) -> None:
    """A document nested far too deeply is invalid YAML, not a RecursionError."""
    deep = "[" * 50_000 + "]" * 50_000
    assert problems(tmp_path, f"kind: rg1\nseeds: {deep}\n")[0].error_type == \
        "Run file is not valid YAML"


@pytest.mark.parametrize("place", ["store: \"\\x67hp_{rest}\"\n",
                                   "\"\\x67hp_{rest}\": 1\n",
                                   "store: \"gh\\\n  p_{rest}\"\n"])
def test_a_token_hidden_by_yaml_escapes_is_still_refused(tmp_path: Path, canary_token: str,
                                                         place: str) -> None:
    """Tokens are found in parsed keys and values too, and never echoed."""
    rest = canary_token[len("ghp_"):]
    text = VALID.replace("store: research.db\n", "") + place.format(rest=rest)
    reported = problems(tmp_path, text)
    assert [p.error_type for p in reported] == ["Run file contains a token"]
    assert rest not in repr(reported)


def test_yaml_merge_keys_are_allowed(tmp_path: Path) -> None:
    """A merge key (<<) is valid YAML and not a duplicate key."""
    text = VALID.replace(
        "      - {start: 2025-01-01, end: 2025-07-01}\n",
        "      - &first {start: 2025-01-01, end: 2025-07-01}\n")
    text += ("  - url: https://github.com/owner/other\n    windows:\n"
             "      - {<<: *first}\n")
    assert check(tmp_path, text).unit_count == 3


def test_a_null_sha_list_means_the_whole_window(tmp_path: Path) -> None:
    """commits: null is "not given", like head, key_file and cutoff."""
    text = VALID.replace(f"commits: [{SHA_A}, {SHA_B}]", "commits: null")
    assert check(tmp_path, text).repositories[0].windows[1].commits is None


@pytest.mark.parametrize("text, ok", [
    ("'2025-07-01'", True), ("'2025-07-01T00:00:00Z'", True), ("'2025-07-01 00:00'", True),
    ("'2025-07-01T00:00:00.5-05:30'", True), ("'20250701'", False), ("'2025-W27-2'", False),
    ("'2025-07-01T00:00:00+24:00'", False), ("'2025-02-30'", False)])
def test_date_text_has_one_grammar_on_every_python(tmp_path: Path, text: str, ok: bool) -> None:
    """Quoted dates follow one explicit grammar, so 3.10 and 3.11 agree (fromisoformat does not)."""
    run_text = VALID.replace("{start: 2025-07-01,", f"{{start: {text},")
    if ok:
        assert check(tmp_path, run_text).unit_count == 2
    else:
        assert "Repository 1: window 2: start must be a date or a UTC timestamp." in \
            causes(tmp_path, run_text)


def test_engine_check_url_applies_fr02_and_returns_the_repository() -> None:
    """ResearchEngine.check_url is the FR-02 pattern: owner and name, or a three-part error."""
    checked = ResearchEngine().check_url("https://github.com/owner/name/")
    assert (checked.owner, checked.name, checked.full_name) == ("owner", "name", "owner/name")
    with pytest.raises(UrlRejected) as caught:
        ResearchEngine().check_url("https://gitlab.com/owner/name")
    assert caught.value.error_type == "Invalid repository URL"
    assert caught.value.cause and caught.value.action


@pytest.mark.parametrize("old, new, cause", [
    ("store: research.db", "store: 5", "store must be a file path."),
    ("store: research.db", "store: '  '", "store must be a file path."),
    ("key_file: ~/.tdea/research.key", "key_file: 5", "key_file must be a file path or null."),
    ("cutoff: null", "cutoff: soon", "cutoff must be a UTC timestamp or null."),
])
def test_values_of_the_wrong_shape_are_rejected(tmp_path: Path, old: str, new: str,
                                                cause: str) -> None:
    """Paths and the cut-off must have the schema's shapes."""
    assert causes(tmp_path, VALID.replace(old, new)) == [cause]


@pytest.mark.parametrize("repositories, cause", [
    ("repositories: []", "repositories must be a non-empty list."),
    ("repositories: owner/name", "repositories must be a non-empty list."),
    ("repositories: [owner/name]",
     "Repository 1: each repository must be a mapping with url and windows."),
    ("repositories: [{windows: [{start: 2025-01-01, end: 2025-02-01}]}]",
     "Repository 1: The required key 'url' is missing."),
    ("repositories: [{url: 'https://github.com/owner/name', windows: []}]",
     "Repository 1: windows must be a non-empty list."),
    ("repositories: [{url: 'https://github.com/owner/name', windows: [x]}]",
     "Repository 1: window 1: each window must be a mapping with start and end."),
    ("repositories: [{url: 'https://github.com/owner/name', windows: [{start: 2025-01-01}]}]",
     "Repository 1: window 1: The required key 'end' is missing."),
    ("repositories: [{url: 'https://github.com/owner/name', "
     "windows: [{start: later, end: 2025-02-01}]}]",
     "Repository 1: window 1: start must be a date or a UTC timestamp."),
])
def test_repository_and_window_shapes_are_checked(tmp_path: Path, repositories: str,
                                                  cause: str) -> None:
    """Each repository is a mapping with url and windows; each window has start and end."""
    head, _rest = VALID.split("repositories:\n", 1)
    assert causes(tmp_path, head + repositories + "\n") == [cause]


def test_keys_that_are_not_plain_names_are_described_not_echoed(tmp_path: Path) -> None:
    """An odd key (long, spaced or not a string) is described rather than repeated."""
    reported = causes(tmp_path, VALID + "this key has spaces: 1\n7: 2\n")
    assert reported == ["A key that is not a plain name is not part of the RG1 run-file "
                        "schema."] * 2


def test_a_complex_yaml_key_is_a_yaml_error(tmp_path: Path) -> None:
    """A YAML mapping used as a key cannot be a run-file key; the parser reports it."""
    reported = problems(tmp_path, VALID + "? {a: 1}\n: 2\n")
    assert reported[0].error_type == "Run file is not valid YAML"


def test_run_file_invalid_needs_a_problem() -> None:
    """The exception always carries at least one three-part problem."""
    with pytest.raises(ValueError):
        _ = RunFileInvalid([])
        