"""CL-03 rule lists v0.1: partial, cited and versioned; never claimed complete."""

from __future__ import annotations

import dataclasses
import re

from tdea.analyzer.research import rule_lists
from tdea.analyzer.research.rule_lists import ALL_LISTS, RuleEntry

PERMITTED_SOURCES = {rule_lists.P2_ATTRIBUTION, rule_lists.P2_FILES, rule_lists.SRS_D4,
                     rule_lists.RG1_PROTOCOL}
EXPECTED_VALUES = {
    "ai_tool_patterns": ("copilot-swe-agent[bot]", "noreply@anthropic.com", "Cursor Agent"),
    "non_ai_bots": ("dependabot", "renovate", "pre-commit-ci", "github-actions"),
    "package_filter": ("test/", "__tests__/", "*_test.py"),
    "bulk_config_files": ("pyproject.toml", "setup.cfg", "tox.ini"),
    "bulk_message_patterns": ("black", "ruff", "isort", "autopep8", "pre-commit", "format",
                              "lint"),
}


def test_every_list_is_marked_incomplete_with_its_gaps() -> None:
    """Version 0.1 claims no list is complete, and each says what is missing."""
    for rule_list in ALL_LISTS:
        assert rule_list.complete is False
        assert rule_list.version == "0.1"
        assert rule_list.gaps


def test_every_entry_cites_a_permitted_source() -> None:
    """Entries come only from P2's paper, SRS D-4 or the RG1 protocol."""
    for rule_list in ALL_LISTS:
        for entry in rule_list.entries:
            assert entry.source in PERMITTED_SOURCES, entry.entry_id


def test_entries_are_exactly_the_published_ones() -> None:
    """No entry is added or changed without a new version (and this test changing)."""
    assert {rule_list.name: tuple(entry.value for entry in rule_list.entries)
            for rule_list in ALL_LISTS} == EXPECTED_VALUES


def test_entry_ids_are_unique() -> None:
    """Pattern IDs are unique across lists, since FR-54 records them."""
    ids = [entry.entry_id for rule_list in ALL_LISTS for entry in rule_list.entries]
    assert len(ids) == len(set(ids))


def test_content_hash_identifies_the_exact_content() -> None:
    """The hash is SHA-256, stable, and changes when any entry changes."""
    original = rule_lists.AI_TOOL_PATTERNS
    assert re.fullmatch(r"[0-9a-f]{64}", original.content_hash())
    assert original.content_hash() == rule_lists.AI_TOOL_PATTERNS.content_hash()
    changed = dataclasses.replace(
        original, entries=original.entries[:2] + (RuleEntry("AI-003", "author name", "X",
                                                            rule_lists.P2_ATTRIBUTION),))
    assert changed.content_hash() != original.content_hash()


def test_manifest_names_every_list_with_version_and_hash() -> None:
    """Run metadata (FR-52) can record which list versions a run used."""
    manifest = rule_lists.list_manifest()
    assert set(manifest) == set(EXPECTED_VALUES)
    for rule_list in ALL_LISTS:
        assert manifest[rule_list.name] == {"version": "0.1", "complete": False,
                                            "sha256": rule_list.content_hash()}