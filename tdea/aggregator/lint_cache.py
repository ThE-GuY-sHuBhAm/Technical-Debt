"""LintCache: lintcache.db, the lint results shared by all runs and stores (NFR-25).

lintcache.db holds the two lint-cache entities of Appendix C: lint_cache, keyed by blob
SHA, file name and profile hash, with the lint status (ok or failed) and failure reason
(NULL when ok, else one of FR-51 (e)'s four); and lint_message, one row per message in
order (symbol, message ID, category, message, line, scope name). It is a separate SQLite
file from research.db and tdea.db and is never exported. LintCache implements the
analyzer's LintCachePort, so the analyzer reaches the cache only through that interface
(Freeze section 2). A hit returns exactly what was stored, so it equals the fresh lint
that produced it; any difference in blob, file name or profile hash is a different key.
An entry is written once and never replaced; a value the constraints refuse is raised.
The cache holds no author data (Decision 2).

Each LintCache counts its lookups while it is open: ``hits`` (answered from the cache)
and ``misses`` (not found, so the profile lints the file afresh and stores the result).
The research run reports both on the console when it ends, so a warm run shows its
cache hits (RE-20). The counts are not stored anywhere.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from tdea.aggregator.research_store import open_sqlite
from tdea.analyzer.research.profile import (DECODE_ERROR, FAILED, OK, PYLINT_CRASH,
                                            SYNTAX_ERROR, TIME_OUT, LintMessage, LintResult)

DEFAULT_LINT_CACHE = Path.home() / ".tdea" / "lintcache.db"  # the run file's default
_REASONS = ", ".join(f"'{reason}'" for reason in (SYNTAX_ERROR, DECODE_ERROR, PYLINT_CRASH,
                                                  TIME_OUT))  # FR-51 (e)


class LintCacheBase(DeclarativeBase):
    """Declarative base whose metadata is lintcache.db's alone."""


class LintCacheEntry(LintCacheBase):
    """One cached lint result (lint_cache)."""

    __tablename__ = "lint_cache"
    __table_args__ = (
        CheckConstraint(f"status IN ('{OK}', '{FAILED}')", name="ck_status_values"),
        CheckConstraint(f"(status = '{OK}' AND failure_reason IS NULL) OR (status = '{FAILED}' "
                        f"AND failure_reason IS NOT NULL AND failure_reason IN ({_REASONS}))",
                        name="ck_failure_reason_values"),
    )

    blob_sha: Mapped[str] = mapped_column(String, primary_key=True)
    file_name: Mapped[str] = mapped_column(String, primary_key=True)
    profile_hash: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    failure_reason: Mapped[str | None] = mapped_column(String)


class LintMessageRow(LintCacheBase):
    """One message of a cached result (lint_message), keyed by its entry and ordinal."""

    __tablename__ = "lint_message"
    __table_args__ = (ForeignKeyConstraint(
        ["blob_sha", "file_name", "profile_hash"],
        ["lint_cache.blob_sha", "lint_cache.file_name", "lint_cache.profile_hash"]),)

    blob_sha: Mapped[str] = mapped_column(String, primary_key=True)
    file_name: Mapped[str] = mapped_column(String, primary_key=True)
    profile_hash: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    message_id: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    line: Mapped[int] = mapped_column(Integer)
    scope_name: Mapped[str] = mapped_column(String)


LINT_CACHE_TABLES = tuple(sorted(LintCacheBase.metadata.tables))


class LintCache:
    """lintcache.db, implementing LintCachePort."""

    def __init__(self, path: Path | str = DEFAULT_LINT_CACHE) -> None:
        """Open or create the lint cache at ``path``."""
        self._engine = open_sqlite(path, LintCacheBase.metadata, "lint cache")
        self.hits = 0
        self.misses = 0

    def __enter__(self) -> LintCache:
        """Return the cache for use in a with block."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the cache."""
        self.close()

    def get(self, blob_sha: str, file_name: str, profile_hash: str) -> LintResult | None:
        """Return the stored result for exactly this key, or None."""
        with Session(self._engine) as session:
            entry = session.get(LintCacheEntry, (blob_sha, file_name, profile_hash))
            if entry is None:
                self.misses += 1
                return None
            self.hits += 1
            rows = session.scalars(select(LintMessageRow).filter_by(
                blob_sha=blob_sha, file_name=file_name, profile_hash=profile_hash,
            ).order_by(LintMessageRow.ordinal)).all()
            return LintResult(entry.status, entry.failure_reason, tuple(
                LintMessage(row.symbol, row.message_id, row.category, row.message, row.line,
                            row.scope_name) for row in rows))

    def put(self, blob_sha: str, file_name: str, profile_hash: str,
            result: LintResult) -> None:
        """Store ``result`` under the key unless an entry already exists."""
        try:
            with Session(self._engine) as session, session.begin():
                if session.get(LintCacheEntry, (blob_sha, file_name, profile_hash)) is not None:
                    return
                _add(session, blob_sha, file_name, profile_hash, result)
        except IntegrityError:
            with Session(self._engine) as session:
                stored = session.get(LintCacheEntry, (blob_sha, file_name, profile_hash))
            if stored is None:
                raise  # a refused value, not an entry another run wrote at the same moment

    def close(self) -> None:
        """Release the file."""
        self._engine.dispose()


def _add(session: Session, blob_sha: str, file_name: str, profile_hash: str,
         result: LintResult) -> None:
    """Add one entry and its messages to ``session``."""
    session.add(LintCacheEntry(blob_sha=blob_sha, file_name=file_name,
                               profile_hash=profile_hash, status=result.status,
                               failure_reason=result.failure_reason))
    session.flush()
    session.add_all(
        LintMessageRow(blob_sha=blob_sha, file_name=file_name, profile_hash=profile_hash,
                       ordinal=ordinal, symbol=message.symbol, message_id=message.message_id,
                       category=message.category, message=message.message, line=message.line,
                       scope_name=message.scope)
        for ordinal, message in enumerate(result.messages))
    
