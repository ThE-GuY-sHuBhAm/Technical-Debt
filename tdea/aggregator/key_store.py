"""KeyStore: the pseudonymisation key file and its fingerprint (NFR-27, Decision 2).

The key is 256 random bits in a local file, created on first use with owner-only
permissions (mode 0600 on Linux and macOS; on Windows the default file lies inside the
user profile). Its fingerprint, the first 16 hex characters of SHA-256 of the key, is
what run metadata and exports record. The key itself is never logged, printed, stored
in the research store or exported; this class keeps it out of its repr.

The aggregator owns the key and hands the bytes down to the analyzer's Pseudonymiser,
so the analyzer never imports this module (NFR-14). The key file holds the 32 key
bytes and nothing else. A new key is written to a private temporary file in the same
folder and then linked into place without overwriting, so a crash never leaves a short
key file and two processes starting at once end up with the same key. An existing file
of any other length, or one that group or other users can read or write on Linux or
macOS, is refused and never replaced: a new key would silently break linkage with
earlier runs (risk R9). TDEA has no key rotation.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from pathlib import Path

KEY_BYTES = 32  # 256 bits
DEFAULT_KEY_FILE = Path.home() / ".tdea" / "research.key"  # the run file's key_file default
_POSIX = os.name != "nt"


class KeyFileError(Exception):
    """The key file exists but cannot be used; its content is never part of the message."""


class KeyStore:
    """Loads, or on first use creates, the pseudonymisation key file."""

    def __init__(self, path: Path | str = DEFAULT_KEY_FILE) -> None:
        """Use the key file at ``path``; nothing is read or created until first use."""
        self._path = Path(path).expanduser()
        self._key: bytes | None = None

    def __repr__(self) -> str:
        """Show the path only; the key never appears."""
        return f"KeyStore(path={str(self._path)!r})"

    @property
    def path(self) -> Path:
        """Return the key file's path."""
        return self._path

    def key(self) -> bytes:
        """Return the 32 key bytes, creating the key file on first use."""
        if self._key is None:
            if not self._path.exists():
                self._create()
            self._key = self._load()
        return self._key

    def fingerprint(self) -> str:
        """Return the first 16 hex characters of SHA-256 of the key."""
        return hashlib.sha256(self.key()).hexdigest()[:16]

    def _create(self) -> None:
        """Write a new random key with owner-only permissions; never overwrite a file."""
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            with os.fdopen(os.open(temporary, flags, 0o600), "wb") as handle:
                handle.write(secrets.token_bytes(KEY_BYTES))
                handle.flush()
                os.fsync(handle.fileno())
            if _POSIX:
                os.chmod(temporary, 0o600)
                os.link(temporary, self._path)  # fails if the key file exists
            else:
                os.rename(temporary, self._path)  # on Windows, fails if it exists
        except FileExistsError:
            pass  # created at the same moment by another process: use that one
        finally:
            temporary.unlink(missing_ok=True)
        if _POSIX:  # make the new directory entry durable before any key is used
            folder = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)

    def _load(self) -> bytes:
        """Read and check the key file."""
        if _POSIX and stat.S_IMODE(self._path.stat().st_mode) & 0o077:
            raise KeyFileError(
                f"The key file {self._path} can be read or written by other users; "
                "restrict it to its owner (chmod 600) or restore it from your backup."
            )
        key = self._path.read_bytes()
        if len(key) != KEY_BYTES:
            raise KeyFileError(
                f"The key file {self._path} does not hold a {KEY_BYTES * 8}-bit key; "
                "restore it from your backup. TDEA does not replace an existing key file."
            )
        return key
    
