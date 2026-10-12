"""AnalysisLock: one active analysis at a time, product or research (CON-03, OI-04).

The lock is an OS advisory lock on one file, taken with ``fcntl.flock`` on Linux and
macOS and ``msvcrt.locking`` on Windows (Freeze section 2), and released when the
process exits, however it ends: the operating system drops the lock with the process,
so a killed run never leaves a stale lock behind. A second analysis that finds the lock
held is rejected at once with a clear three-part message (OI-04: reject, never queue);
the research command line maps that refusal to exit 3 (FR-48).

Location (Task 7 Day 6 decision 2a): ``analysis.lock`` in a TDEA-owned per-user folder
of the system temporary directory, ``tdea-<user>``, which NFR-26 reads as part of TDEA's
temporary directory. On Linux and macOS the folder is named by the numeric user ID,
created with mode 0700 and refused unless it is a real folder owned by the user with
no group or other permissions, so another user cannot take, replace or redirect the
lock; the lock file is opened without following a symbolic link and created with mode
0600. On Windows the system temporary directory is already inside the user profile and
the folder is named by the account name. The lock file is never deleted, because
deleting a lock file while another process opens it would let two analyses run at
once; it holds no data.

A folder or file that cannot be used safely is refused as the lock being unavailable,
with the same exit code as a held lock: the single-analysis guarantee cannot be given.
The product's AnalysisService takes the same lock when it exists; nothing in the product
pipeline changes here.
"""

from __future__ import annotations

import errno
import getpass
import os
import re
import stat
import tempfile
from pathlib import Path
from types import TracebackType

LOCK_FILE_NAME = "analysis.lock"
EXIT_REFUSED = 3
_POSIX = os.name != "nt"

if _POSIX:
    import fcntl  # pylint: disable=import-error  # POSIX only
else:
    import msvcrt  # pylint: disable=import-error  # Windows only


class LockUnavailable(Exception):
    """The single-analysis lock cannot be taken; carries the three FR-05 parts."""

    exit_code = EXIT_REFUSED

    def __init__(self, error_type: str, cause: str, action: str) -> None:
        """Keep the three parts of the refusal."""
        super().__init__(f"{error_type}: {cause} {action}")
        self.error_type = error_type
        self.cause = cause
        self.action = action


class LockHeld(LockUnavailable):
    """Another analysis holds the lock (CON-03, OI-04)."""

    def __init__(self, path: Path) -> None:
        """Name the lock file; the other process is not identified."""
        super().__init__(
            "Another analysis is running",
            f"TDEA runs one analysis at a time, and another process holds the analysis "
            f"lock ({path}).",
            "Wait for the other analysis to finish, then start this one again.")


def default_lock_directory() -> Path:
    """Return the per-user TDEA folder in the system temporary directory."""
    return Path(tempfile.gettempdir()) / f"tdea-{_user_id()}"


def _user_id() -> str:
    """Return the numeric user ID on POSIX, else the account name made safe for a path."""
    if _POSIX:
        return str(os.getuid())
    name = re.sub(r"[^A-Za-z0-9._-]", "_", getpass.getuser())
    return name or "user"


class AnalysisLock:
    """The CON-03 lock: acquire() or use as a context manager; release() is idempotent."""

    def __init__(self, directory: Path | str | None = None) -> None:
        """Lock ``analysis.lock`` in ``directory`` (default: the per-user TDEA folder)."""
        self._directory = Path(directory) if directory is not None \
            else default_lock_directory()
        self._descriptor: int | None = None

    @property
    def path(self) -> Path:
        """Return the lock file's path."""
        return self._directory / LOCK_FILE_NAME

    @property
    def held(self) -> bool:
        """Return True while this object holds the lock."""
        return self._descriptor is not None

    def acquire(self) -> None:
        """Take the lock or raise LockHeld; never wait (OI-04: reject)."""
        if self._descriptor is not None:
            raise RuntimeError("this AnalysisLock is already held")
        self._prepare_directory()
        descriptor = self._open()
        try:
            _lock(descriptor)
        except OSError as exc:
            os.close(descriptor)
            if _held_elsewhere(exc):
                raise LockHeld(self.path) from None
            raise _unusable(self.path, "cannot be locked") from None
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def release(self) -> None:
        """Release the lock if this object holds it."""
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            _unlock(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> AnalysisLock:
        """Take the lock for a with block."""
        self.acquire()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        """Release the lock whether the block succeeded or raised."""
        self.release()

    def _prepare_directory(self) -> None:
        """Create the lock folder if needed and refuse one that is not safely ours."""
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = os.lstat(self._directory)
        except OSError:
            raise _unusable(self._directory, "cannot be created or read") from None
        if not stat.S_ISDIR(info.st_mode):
            raise _unusable(self._directory, "is not a folder")
        if _POSIX and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise _unusable(self._directory,
                            "is not owned by this user with owner-only permissions")

    def _open(self) -> int:
        """Open (or create) the lock file without following a symbolic link."""
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) \
            | getattr(os, "O_BINARY", 0)
        try:
            return os.open(self.path, flags, 0o600)
        except OSError:
            raise _unusable(self.path, "cannot be opened as a plain file") from None


def _unusable(path: Path, problem: str) -> LockUnavailable:
    """Return the refusal for a lock folder or file that cannot be used safely."""
    return LockUnavailable(
        "Analysis lock unavailable",
        f"The analysis lock at {path} {problem}, so TDEA cannot guarantee that only one "
        "analysis runs at a time (CON-03).",
        "Remove or repair that path, then start the analysis again.")


def _lock(descriptor: int) -> None:
    """Take an exclusive, non-blocking lock; raise OSError if another holder has it."""
    if _POSIX:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)


def _held_elsewhere(error: OSError) -> bool:
    """Return True if a failed lock attempt means another holder has the lock."""
    if _POSIX:
        return isinstance(error, BlockingIOError)
    return error.errno in (errno.EACCES, errno.EDEADLK)


def _unlock(descriptor: int) -> None:
    """Release the lock taken by _lock()."""
    if _POSIX:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    else:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
