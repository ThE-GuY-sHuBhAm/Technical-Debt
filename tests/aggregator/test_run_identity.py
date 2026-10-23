"""The run identifier and canonical run file (FR-48, FR-52; Task 7 Day 6 decision 4).

Checks: the formula, written out by hand; determinism; store, key_file and lint_cache left out;
representation-only differences giving one identifier; every parameter giving a new
one; and no merging of inputs nothing authoritative calls equal (list order, URL form).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from run_files import schema_example

from tdea.aggregator.research_service import ResearchService
from tdea.aggregator.run_file import RunFile
from tdea.aggregator.run_identity import (IDENTIFIER_LENGTH, canonical_text, instant_text,
                                          run_identifier)

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
PROFILE = "p" * 64
VERSION = "0.1.0"
FINGERPRINT = "f" * 16
SHA_A, SHA_B = "a" * 40, "b" * 40
EXPECTED_CANONICAL = (
    '{"cutoff":null,"kind":"rg1","repositories":[{"head":null,'
    '"url":"https://github.com/owner/name","windows":['
    '{"commits":null,"end":"2025-07-01T00:00:00Z","start":"2025-01-01T00:00:00Z"},'
    '{"commits":null,"end":"2026-01-01T00:00:00Z","start":"2025-07-01T00:00:00Z"}]}],'
    '"seeds":{"sampling":20261005},"tau":0.7,"w_months":6}'
)


def parse(tmp_path: Path, text: str) -> RunFile:
    """Validate ``text`` as a run file (no side effects) and return it."""
    path = tmp_path / f"run-{hashlib.sha256(text.encode()).hexdigest()[:8]}.yaml"
    path.write_text(text, encoding="utf-8")
    return ResearchService(clock=lambda: NOW).validate(path)


def identifier(run_file: RunFile, profile: str = PROFILE, version: str = VERSION,
               fingerprint: str = FINGERPRINT) -> str:
    """Return the identifier under the test parameters."""
    return run_identifier(run_file, profile, version, fingerprint)


def test_the_formula_written_out(tmp_path: Path) -> None:
    """SHA-256 over canonical JSON of the four parts, first 12 hex characters."""
    run_file = parse(tmp_path, schema_example())
    assert canonical_text(run_file) == EXPECTED_CANONICAL
    payload = ('{"canonical_run_file":' + EXPECTED_CANONICAL + f',"key_fingerprint":'
               f'"{FINGERPRINT}","profile_hash":"{PROFILE}","tdea_version":"{VERSION}"}}')
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    assert identifier(run_file) == expected
    assert len(expected) == IDENTIFIER_LENGTH == 12


def test_the_identifier_is_deterministic(tmp_path: Path) -> None:
    """The same file and parameters give the same identifier every time."""
    first = identifier(parse(tmp_path, schema_example()))
    assert all(identifier(parse(tmp_path, schema_example())) == first for _ in range(3))
    assert all(char in "0123456789abcdef" for char in first)


@pytest.mark.parametrize("old, new", [
    ("store: research.db", "store: other/place.db"),
    ("key_file: ~/.tdea/research.key", "key_file: /elsewhere/k.key"),
    ("key_file: ~/.tdea/research.key\n", ""),
    ("key_file: ~/.tdea/research.key\n",
     "key_file: ~/.tdea/research.key\nlint_cache: ~/.tdea/lintcache.db\n"),
    ("key_file: ~/.tdea/research.key\n",
     "key_file: ~/.tdea/research.key\nlint_cache: /elsewhere/cache.db\n"),
])
def test_store_key_file_and_lint_cache_are_not_part_of_it(tmp_path: Path, old: str,
                                                          new: str) -> None:
    """FR-48: store, key_file and lint_cache are left out of the canonical run file and id."""
    base, changed = parse(tmp_path, schema_example()), \
        parse(tmp_path, schema_example().replace(old, new))
    assert canonical_text(changed) == canonical_text(base)
    assert identifier(changed) == identifier(base)


@pytest.mark.parametrize("old, new", [
    ("kind: rg1\n", "kind: rg1   # an RG1 run\n"),
    ("tau: 0.7\ncutoff: null\n", "cutoff: null\ntau: 0.70\n"),
    ("{start: 2025-01-01, end: 2025-07-01}", "{end: 2025-07-01, start: '2025-01-01'}"),
    ("start: 2025-01-01,", "start: 2025-01-01T00:00:00Z,"),
    ("start: 2025-01-01,", "start: 2025-01-01 05:30:00+05:30,"),
    ("cutoff: null\n", ""),
    ("    head: null\n", ""),
    ("{start: 2025-07-01, end: 2026-01-01}", "{start: 2025-07-01, end: 2026-01-01, commits: null}"),
    ("seeds: {sampling: 20261005}", "seeds:\n  sampling: 20261005"),
])
def test_representation_only_differences_give_one_identifier(tmp_path: Path, old: str,
                                                             new: str) -> None:
    """Layout, comments, key order, instant spelling and absent-versus-null do not count."""
    text = schema_example().replace(old, new)
    assert text != schema_example()
    assert identifier(parse(tmp_path, text)) == identifier(parse(tmp_path, schema_example()))


@pytest.mark.parametrize("old, new", [
    ("w_months: 6", "w_months: 9"),  # RE-06 step 4: W = 9 is a new run
    ("tau: 0.7", "tau: 0.8"),
    ("sampling: 20261005", "sampling: 20261006"),
    ("cutoff: null", "cutoff: 2026-01-01T00:00:00Z"),
    ("end: 2026-01-01}", "end: 2026-01-02}"),
    ("head: null", f"head: {SHA_A}"),
    ("{start: 2025-07-01, end: 2026-01-01}",
     f"{{start: 2025-07-01, end: 2026-01-01, commits: [{SHA_A}]}}"),
])
def test_every_run_file_parameter_gives_a_new_identifier(tmp_path: Path, old: str,
                                                          new: str) -> None:
    """Any change to a recorded parameter is a new run (FR-52, RE-06)."""
    base = identifier(parse(tmp_path, schema_example()))
    assert identifier(parse(tmp_path, schema_example().replace(old, new))) != base


def test_profile_version_and_key_give_new_identifiers(tmp_path: Path) -> None:
    """The profile hash, TDEA version and key fingerprint are each part of the identifier."""
    run_file = parse(tmp_path, schema_example())
    identifiers = {identifier(run_file), identifier(run_file, profile="q" * 64),
                   identifier(run_file, version="0.1.1"),
                   identifier(run_file, fingerprint="e" * 16)}
    assert len(identifiers) == 4


@pytest.mark.parametrize("bad", ["", None])
def test_missing_parameters_are_refused(tmp_path: Path, bad: object) -> None:
    """An empty profile hash, version or fingerprint is a programming error."""
    run_file = parse(tmp_path, schema_example())
    for arguments in ((bad, VERSION, FINGERPRINT), (PROFILE, bad, FINGERPRINT),
                      (PROFILE, VERSION, bad)):
        with pytest.raises(ValueError):
            run_identifier(run_file, *arguments)  # type: ignore[arg-type]


TWO_REPOSITORIES = (
    "kind: rg1\nstore: research.db\nseeds: {sampling: 1}\nw_months: 6\ntau: 0.7\n"
    "repositories:\n"
    "  - url: https://github.com/owner/first\n"
    "    windows:\n"
    "      - {start: 2025-01-01, end: 2025-07-01}\n"
    "      - {start: 2025-07-01, end: 2026-01-01, commits: [SHA1, SHA2]}\n"
    "  - url: https://github.com/owner/second\n"
    "    windows: [{start: 2025-01-01, end: 2025-07-01}]\n"
).replace("SHA1", SHA_A).replace("SHA2", SHA_B)


@pytest.mark.parametrize("old, new", [
    ("https://github.com/owner/first", "http://github.com/owner/first"),
    ("https://github.com/owner/first", "https://github.com/owner/first/"),
    ("https://github.com/owner/first", "https://github.com/Owner/First"),
    (f"[{SHA_A}, {SHA_B}]", f"[{SHA_B}, {SHA_A}]"),
])
def test_inputs_without_an_authoritative_equivalence_are_not_merged(
    tmp_path: Path, old: str, new: str
) -> None:
    """Decision 4: URL forms and SHA order are kept as written, so the identifiers differ."""
    changed = TWO_REPOSITORIES.replace(old, new)
    assert changed != TWO_REPOSITORIES
    assert identifier(parse(tmp_path, changed)) != identifier(parse(tmp_path, TWO_REPOSITORIES))


def test_repository_and_window_order_are_kept(tmp_path: Path) -> None:
    """Swapping repositories or windows is a different run file and a different identifier."""
    swapped_repositories = (
        "kind: rg1\nstore: research.db\nseeds: {sampling: 1}\nw_months: 6\ntau: 0.7\n"
        "repositories:\n"
        "  - url: https://github.com/owner/second\n"
        "    windows: [{start: 2025-01-01, end: 2025-07-01}]\n"
        "  - url: https://github.com/owner/first\n"
        "    windows:\n"
        "      - {start: 2025-01-01, end: 2025-07-01}\n"
        f"      - {{start: 2025-07-01, end: 2026-01-01, commits: [{SHA_A}, {SHA_B}]}}\n")
    swapped_windows = schema_example().replace(
        "      - {start: 2025-01-01, end: 2025-07-01}\n      - {start: 2025-07-01, end: 2026-01-01}",
        "      - {start: 2025-07-01, end: 2026-01-01}\n      - {start: 2025-01-01, end: 2025-07-01}")
    assert swapped_windows != schema_example()
    base = identifier(parse(tmp_path, TWO_REPOSITORIES))
    assert identifier(parse(tmp_path, swapped_repositories)) != base
    assert identifier(parse(tmp_path, swapped_windows)) != identifier(
        parse(tmp_path, schema_example()))


def test_instants_are_utc_with_z() -> None:
    """Instants are written in UTC with Z; a naive instant is refused."""
    assert instant_text(datetime(2025, 1, 1, 5, 30, tzinfo=timezone.utc)) == \
        "2025-01-01T05:30:00Z"
    assert instant_text(None) is None
    with pytest.raises(ValueError):
        instant_text(datetime(2025, 1, 1))
