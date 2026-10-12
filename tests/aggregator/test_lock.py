"""AnalysisLock (CON-03, OI-04, FR-48 exit 3): one analysis at a time, released with the process.

The lock is taken in a test folder, never the real per-user folder, so a TDEA run on the
same machine is not disturbed. The cross-process checks start a real second Python
process that holds the lock, then kill it: the operating system must release the lock
with the process, as the Freeze requires.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tdea.aggregator.lock import (EXIT_REFUSED, LOCK_FILE_NAME, AnalysisLock, LockHeld,
                                  LockUnavailable, default_lock_directory)
from tdea.api.api_error import ApiError

POSIX = os.name != "nt"
HOLDER = (
    "import sys, time\n"
    "from tdea.aggregator.lock import AnalysisLock\n"
    "lock = AnalysisLock(sys.argv[1])\n"
    "lock.acquire()\n"
    "print('locked', flush=True)\n"
    "time.sleep(120)\n"
)


def start_holder(directory: Path) -> subprocess.Popen[str]:
    """Start a second process that takes the lock and waits; return once it holds it."""
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(directory)],
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "locked"
    return process


def acquire_within(lock: AnalysisLock, seconds: float) -> None:
    """Acquire ``lock``, retrying while the OS finishes releasing a killed holder's lock."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            lock.acquire()
            return
        except LockHeld:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_acquire_release_and_context_manager(tmp_path: Path) -> None:
    """The lock is held inside the block and free after it, also after an exception."""
    lock = AnalysisLock(tmp_path)
    assert lock.path == tmp_path / LOCK_FILE_NAME and not lock.held
    with lock:
        assert lock.held
    assert not lock.held
    with pytest.raises(ZeroDivisionError):
        with lock:
            raise ZeroDivisionError
    assert not lock.held
    with AnalysisLock(tmp_path):  # free again for a new holder
        pass


def test_a_second_analysis_is_rejected_not_queued(tmp_path: Path) -> None:
    """OI-04: a second lock attempt fails at once with a three-part refusal (exit 3)."""
    with AnalysisLock(tmp_path):
        started = time.monotonic()
        with pytest.raises(LockHeld) as caught:
            AnalysisLock(tmp_path).acquire()
        assert time.monotonic() - started < 5
    refusal = ApiError.wrap(caught.value)
    assert caught.value.exit_code == EXIT_REFUSED == 3
    assert refusal.error_type == "Another analysis is running"
    assert "one analysis at a time" in refusal.cause and str(tmp_path) in refusal.cause
    assert refusal.action.startswith("Wait for the other analysis")


def test_release_is_idempotent_and_reacquiring_one_object_is_refused(tmp_path: Path) -> None:
    """release() twice is harmless; acquire() on a held object is a programming error."""
    lock = AnalysisLock(tmp_path)
    lock.acquire()
    with pytest.raises(RuntimeError):
        lock.acquire()
    lock.release()
    lock.release()
    assert not lock.held


def test_a_lock_held_by_another_process_is_released_when_it_is_killed(tmp_path: Path) -> None:
    """CON-03 across processes; a killed holder leaves no stale lock (Freeze section 2)."""
    holder = start_holder(tmp_path)
    try:
        with pytest.raises(LockHeld):
            AnalysisLock(tmp_path).acquire()
    finally:
        holder.kill()
        holder.wait(timeout=30)
        if holder.stdout is not None:
            holder.stdout.close()
    lock = AnalysisLock(tmp_path)
    acquire_within(lock, 10)
    lock.release()


def test_default_folder_is_per_user_in_the_system_temporary_directory() -> None:
    """Decision 2a: <temp>/tdea-<user>/analysis.lock; only computed, never created here."""
    folder = default_lock_directory()
    assert folder.parent == Path(tempfile.gettempdir())
    expected = f"tdea-{os.getuid()}" if POSIX else None
    if expected is not None:
        assert folder.name == expected
    else:
        assert folder.name.startswith("tdea-") and len(folder.name) > len("tdea-")
    assert AnalysisLock().path == folder / LOCK_FILE_NAME


@pytest.mark.skipif(not POSIX, reason="owner-only permissions are a POSIX check")
def test_the_folder_is_created_owner_only(tmp_path: Path) -> None:
    """A new lock folder has mode 0700 and the lock file mode 0600."""
    folder = tmp_path / "tdea-test"
    with AnalysisLock(folder):
        assert folder.stat().st_mode & 0o777 == 0o700
        assert (folder / LOCK_FILE_NAME).stat().st_mode & 0o077 == 0


@pytest.mark.skipif(not POSIX, reason="owner-only permissions are a POSIX check")
def test_a_folder_others_can_use_is_refused(tmp_path: Path) -> None:
    """A lock folder with group or other permissions is unusable, never silently used."""
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    with pytest.raises(LockUnavailable) as caught:
        AnalysisLock(folder).acquire()
    assert not isinstance(caught.value, LockHeld)
    assert caught.value.exit_code == EXIT_REFUSED
    assert "owner-only" in caught.value.cause
    assert not (folder / LOCK_FILE_NAME).exists()


def test_a_file_in_place_of_the_folder_is_refused(tmp_path: Path) -> None:
    """A file where the lock folder should be is refused."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_bytes(b"")
    with pytest.raises(LockUnavailable, match="not a folder|cannot be created"):
        AnalysisLock(blocker).acquire()


@pytest.mark.skipif(not POSIX, reason="O_NOFOLLOW is a POSIX flag")
def test_a_symbolic_link_in_place_of_the_lock_file_is_not_followed(tmp_path: Path) -> None:
    """A planted link at analysis.lock is refused; its target is never created or touched."""
    folder = tmp_path / "locks"
    folder.mkdir(mode=0o700)
    target = tmp_path / "elsewhere"
    (folder / LOCK_FILE_NAME).symlink_to(target)
    with pytest.raises(LockUnavailable, match="plain file"):
        AnalysisLock(folder).acquire()
    assert not target.exists()
