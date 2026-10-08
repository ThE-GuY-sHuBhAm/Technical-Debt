"""KeyStore (NFR-27): key creation, persistence, permissions, fingerprint and secrecy.

These tests need no Pylint, so they sit outside tests/research and run in every CI cell.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

import pytest

from tdea.aggregator import key_store
from tdea.aggregator.key_store import DEFAULT_KEY_FILE, KEY_BYTES, KeyFileError, KeyStore
from tdea.analyzer.research.rg1.pseudonymiser import IdentityKind, Pseudonymiser

POSIX = os.name != "nt"


def test_first_use_creates_a_256_bit_key(tmp_path: Path) -> None:
    """A missing key file and its folders are created on first use with 32 random bytes."""
    path = tmp_path / "nested" / ".tdea" / "research.key"
    store = KeyStore(path)
    assert not path.exists()  # nothing happens before first use
    key = store.key()
    assert len(key) == KEY_BYTES == 32
    assert path.read_bytes() == key


def test_key_persists_across_instances(tmp_path: Path) -> None:
    """A second KeyStore on the same file returns the same key and never rewrites it."""
    path = tmp_path / "research.key"
    first = KeyStore(path).key()
    modified = path.stat().st_mtime_ns
    assert KeyStore(path).key() == first
    assert path.stat().st_mtime_ns == modified


def test_two_files_hold_different_keys(tmp_path: Path) -> None:
    """Each new key file gets its own random key."""
    assert KeyStore(tmp_path / "a.key").key() != KeyStore(tmp_path / "b.key").key()


@pytest.mark.skipif(not POSIX, reason="file modes apply on Linux and macOS")
def test_key_file_is_owner_only(tmp_path: Path) -> None:
    """The key file is created with mode 0600 whatever the umask."""
    previous = os.umask(0o022)
    try:
        path = tmp_path / "research.key"
        KeyStore(path).key()
    finally:
        os.umask(previous)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(not POSIX, reason="file modes apply on Linux and macOS")
def test_key_file_readable_by_others_is_refused(tmp_path: Path) -> None:
    """A key file that group or others can read is refused, not used or replaced."""
    path = tmp_path / "research.key"
    KeyStore(path).key()
    content = path.read_bytes()
    path.chmod(0o644)
    with pytest.raises(KeyFileError, match="other users"):
        KeyStore(path).key()
    assert path.read_bytes() == content


def test_default_key_file_is_in_the_user_profile() -> None:
    """The default key file is ~/.tdea/research.key (inside the user profile on Windows)."""
    assert DEFAULT_KEY_FILE == Path.home() / ".tdea" / "research.key"


def test_fingerprint_is_first_16_hex_of_sha256(tmp_path: Path) -> None:
    """Same key, same fingerprint; another key, another fingerprint."""
    path = tmp_path / "research.key"
    store = KeyStore(path)
    store.key()
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert store.fingerprint() == expected[:16]
    assert KeyStore(path).fingerprint() == store.fingerprint()
    assert KeyStore(tmp_path / "other.key").fingerprint() != store.fingerprint()
    assert len(store.fingerprint()) == 16


@pytest.mark.parametrize("content", [b"", b"\x00" * 31, b"\x00" * 33, b"0" * 64 + b"\n"])
def test_corrupt_key_file_is_refused_and_kept(tmp_path: Path, content: bytes) -> None:
    """A file that does not hold exactly 32 bytes is refused and left as it is."""
    path = tmp_path / "research.key"
    path.write_bytes(content)
    if POSIX:
        path.chmod(0o600)
    with pytest.raises(KeyFileError, match="does not hold a 256-bit key"):
        KeyStore(path).key()
    assert path.read_bytes() == content


def test_key_never_appears_in_repr_errors_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Neither the bytes nor their hex form show up in repr, str or any log record."""
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "research.key"
    store = KeyStore(path)
    key = store.key()
    pseudonymiser = Pseudonymiser(key)
    shown = " ".join([repr(store), str(store), repr(pseudonymiser), str(pseudonymiser),
                      store.fingerprint(), caplog.text])
    for secret in (key.hex(), repr(key), key.hex().upper()):
        assert secret not in shown
    path.write_bytes(key[:-1])
    if POSIX:
        path.chmod(0o600)
    with pytest.raises(KeyFileError) as caught:
        KeyStore(path).key()
    assert key[:-1].hex() not in str(caught.value)


def test_aggregator_hands_the_key_down_to_the_pseudonymiser(tmp_path: Path) -> None:
    """The analyzer's Pseudonymiser takes the key bytes; the same key file gives the same keys."""
    path = tmp_path / "research.key"
    first = Pseudonymiser(KeyStore(path).key())
    second = Pseudonymiser(KeyStore(path).key())
    other = Pseudonymiser(KeyStore(tmp_path / "other.key").key())
    email = "asha.rao@example.invalid"
    assert first.identity_key(email, IdentityKind.EMAIL) == second.identity_key(
        email, IdentityKind.EMAIL)
    assert first.identity_key(email, IdentityKind.EMAIL) != other.identity_key(
        email, IdentityKind.EMAIL)


def test_failed_creation_leaves_no_key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while writing a new key leaves neither a short key file nor a temporary file."""
    path = tmp_path / "research.key"

    def broken(_descriptor: int) -> None:
        """Fail the way a crash or a full disk would, after the key bytes are written."""
        raise OSError("disk full")

    monkeypatch.setattr(key_store.os, "fsync", broken)
    with pytest.raises(OSError, match="disk full"):
        KeyStore(path).key()
    assert not list(tmp_path.iterdir())
    monkeypatch.undo()
    assert len(KeyStore(path).key()) == KEY_BYTES


def test_existing_key_file_is_never_overwritten(tmp_path: Path) -> None:
    """If the key file appears while a key is being created, the existing key wins."""
    path = tmp_path / "research.key"
    existing = bytes(range(32))
    store = KeyStore(path)
    path.write_bytes(existing)
    if POSIX:
        path.chmod(0o600)
    store._create()  # pylint: disable=protected-access  # the race, made deterministic
    assert store.key() == existing
    assert [entry.name for entry in tmp_path.iterdir()] == ["research.key"]
    