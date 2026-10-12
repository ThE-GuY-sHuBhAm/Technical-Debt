"""RE-09 step 4 (FR-54, NFR-27), research.db part: no raw identity reaches the store or logs.

FX-RG1 is cloned and its history read, pseudonymised in memory with a key from the
KeyStore, and only then written to a fresh research.db. That is the order NFR-27 requires:
raw strings in memory, then pseudonymisation, then persistence. research.db (its SQL dump
and its raw bytes) and every log record emitted from the clone onwards are then searched
for each raw name, e-mail and e-mail local part of the fixture history, trailers included,
and for the key (hex in either case, and base64). Classification stays
unresolved (CL-03), so commits are stored with an empty trace status (Task 6 decision 4);
no status is claimed. The writing below is test code: the research engine that will do
it in runs arrives on Day 6.
"""

from __future__ import annotations

import base64
import logging
import sqlite3
from pathlib import Path

import make_fx_rg1
import pytest
from fx_rg1_clone import ClonedFxRg1, clone_fx_rg1

import tdea
from tdea.aggregator import research_models as rm
from tdea.aggregator.key_store import KeyStore
from tdea.aggregator.research_store import ResearchStore
from tdea.analyzer.research.profile import ResearchProfile, canonical_profile
from tdea.analyzer.research.rg1.pseudonymiser import IdentityKind, Pseudonymiser
from tdea.git_handler.history_reader import HistoryReader

IDENTITIES = (make_fx_rg1.ASHA, make_fx_rg1.RAVI, make_fx_rg1.JANE, make_fx_rg1.AI_COMMITTER,
              make_fx_rg1.AI_AUTHOR, make_fx_rg1.DEPENDENCY_BOT)
RAW_VALUES = [part for identity in IDENTITIES
              for part in (identity.name, identity.email, identity.email.split("@")[0])
              ] + ["Claude", "noreply@anthropic.com", "noreply"]


def store_fx_rg1(cloned: ClonedFxRg1, store: ResearchStore, key_store: KeyStore) -> None:
    """Write FX-RG1's repository, commits and file changes, pseudonymised first."""
    pseudonymiser = Pseudonymiser(key_store.key())
    reader = HistoryReader(cloned.local)
    with store.transaction() as session:
        session.add(rm.Run(
            run_id="fx-rg1-re09", kind="rg1", status="running", tdea_version=tdea.__version__,
            python_version="3.11", pylint_version="4.1.1", astroid_version="4.3.3",
            radon_version="6.0.1", profile_hash=ResearchProfile().profile_hash(),
            profile=rm.canonical_json(canonical_profile()),
            canonical_run_file=rm.canonical_json({"kind": "rg1"}),
            key_fingerprint=key_store.fingerprint(), rename_threshold=50))
        session.flush()
        repository = rm.Repository(
            run_id="fx-rg1-re09", url=cloned.metadata.url,
            default_branch=cloned.metadata.default_branch,
            clone_time=rm.iso_utc(cloned.metadata.cloned_at), head_sha=cloned.metadata.head_sha,
            cutoff=rm.iso_utc(cloned.history.cutoff))
        session.add(repository)
        session.flush()
        for commit in cloned.history.commits:
            keys = pseudonymiser.pseudonymise(commit.raw)  # before anything is written
            row = rm.Commit(
                repository_id=repository.repository_id, sha=commit.sha,
                first_parent_position=commit.position,
                parent_shas=rm.canonical_json(list(commit.parents)), is_merge=commit.is_merge,
                authored_at=rm.iso_utc(commit.authored_at),
                committed_at=rm.iso_utc(commit.committed_at),
                author_name_key=keys.author_name_key, author_email_key=keys.author_email_key,
                committer_name_key=keys.committer_name_key,
                committer_email_key=keys.committer_email_key, message=keys.message,
                trace_status=None)
            session.add(row)
            session.flush()
            session.add_all(rm.FileChange(
                commit_id=row.commit_id, change_type=change.change_type.value,
                old_path=change.old.path if change.old else None,
                new_path=change.new.path if change.new else None,
                old_blob=change.old.blob if change.old else None,
                new_blob=change.new.blob if change.new else None,
                rename_similarity=change.similarity, is_package=None,
                lines_added=change.lines_added, lines_removed=change.lines_removed,
                hunks=rm.canonical_json([[h.old_start, h.old_length, h.new_start, h.new_length]
                                         for h in change.hunks]))
                for change in reader.file_changes(commit))


@pytest.fixture(name="stored")
def _stored(fx_rg1: make_fx_rg1.FxRg1, tmp_path: Path, caplog: pytest.LogCaptureFixture,
            monkeypatch: pytest.MonkeyPatch) -> tuple[Path, KeyStore, str]:
    """Clone, read and store FX-RG1 in a fresh research.db with every log record captured.

    GitPython's debug records name the working folder, so the work runs from ``tmp_path``
    and the search never depends on where the repository is checked out.
    """
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)
    key_store = KeyStore(tmp_path / "research.key")
    (tmp_path / "clones").mkdir()
    with clone_fx_rg1(fx_rg1, tmp_path / "clones") as cloned, \
            ResearchStore(tmp_path / "research.db") as store:
        store_fx_rg1(cloned, store, key_store)
    return tmp_path / "research.db", key_store, caplog.text


def test_no_raw_identity_in_research_db_or_logs(stored: tuple[Path, KeyStore, str]) -> None:
    """Neither the dump, the file's bytes nor the logs hold a raw identity, or the key."""
    path, key_store, logs = stored
    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    content = path.read_bytes()
    assert "INSERT INTO" in dump and '"commit"' in dump
    for value in RAW_VALUES:
        for text in (dump, logs):
            assert value.casefold() not in text.casefold(), value
        assert value.encode() not in content and value.lower().encode() not in content
    key = key_store.key()
    for text in (dump, logs):
        for form in (key.hex(), base64.b64encode(key).decode(), base64.b32encode(key).decode()):
            assert form.casefold() not in text.casefold()
    assert key not in content and key.hex().encode() not in content


def test_stored_identities_are_keys_and_status_is_unclassified(
    stored: tuple[Path, KeyStore, str], fx_rg1: make_fx_rg1.FxRg1
) -> None:
    """Commits carry the expected identity keys, rewritten trailers and no trace status."""
    path, key_store, _logs = stored
    pseudonymiser = Pseudonymiser(key_store.key())
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            'SELECT sha, author_email_key, trace_status, message FROM "commit" '
            "ORDER BY first_parent_position").fetchall()
    assert [row[0] for row in rows] == fx_rg1.first_parent_shas()
    assert {row[2] for row in rows} == {None}
    by_sha = {row[0]: row for row in rows}
    ravi = pseudonymiser.identity_key(make_fx_rg1.RAVI.email, IdentityKind.EMAIL)
    assert by_sha[fx_rg1.shas["trailers"]][1] == ravi
    asha = pseudonymiser.identity_key(make_fx_rg1.ASHA.email, IdentityKind.EMAIL)
    assert f"Co-authored-by: id:{asha}" in by_sha[fx_rg1.shas["trailers"]][3]
    