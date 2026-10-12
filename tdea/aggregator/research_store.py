"""ResearchStore: research.db, the SQLite file of research records (FR-52, NFR-25).

The research store is a SQLite file separate from the product store (tdea.db, FR-19) and
from the shared lint cache (lintcache.db); no foreign key crosses files. Opening a file
that already holds tables of any other schema is refused, so the three files can never be
mixed, in either direction. Foreign keys are enforced on every connection. Writes go
through transaction(): one transaction, committed on success and rolled back on any
error, which is the unit-of-work NFR-25 asks for. The store holds identity keys only
(NFR-27); it never receives raw identities and never logs record contents.

Run planning and resume belong to ResearchJob (Day 6); this module opens, creates and
transacts. read_only() opens an existing research.db for ``tdea research status``
without writing anything: SQLite's read-only mode, no table created, no journal written,
and a file that is not a research store (or does not exist) is refused.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import MetaData, event, inspect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import URL, Engine, create_engine
from sqlalchemy.orm import Session

from tdea.aggregator.research_models import ResearchBase


def open_sqlite(path: Path | str, metadata: MetaData, purpose: str) -> Engine:
    """Open (or create) a SQLite file for ``metadata`` alone, with foreign keys on."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create("sqlite", database=str(path)))
    event.listen(engine, "connect", _enable_foreign_keys)
    foreign = sorted(set(inspect(engine).get_table_names()) - set(metadata.tables))
    if foreign:
        engine.dispose()
        raise ValueError(f"{path} is not a {purpose}: it holds other tables "
                         f"({', '.join(foreign)}).")
    metadata.create_all(engine)
    return engine


def _enable_foreign_keys(connection, _record) -> None:
    """Turn on SQLite's foreign-key enforcement for a new connection."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class ResearchStore:
    """research.db: the 21 research entities and their transactions."""

    def __init__(self, path: Path | str, *, engine: Engine | None = None) -> None:
        """Open or create the research store at ``path`` (the run file's store key)."""
        self._path = Path(path).expanduser()
        self._engine = engine if engine is not None else open_sqlite(
            self._path, ResearchBase.metadata, "research store")

    @classmethod
    def read_only(cls, path: Path | str) -> ResearchStore:
        """Open an existing research store for reading only; nothing is created or written."""
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{path} does not exist")
        engine = create_engine(URL.create("sqlite", database=path.as_uri(),
                                          query={"mode": "ro", "uri": "true"}))
        try:
            foreign = sorted(set(inspect(engine).get_table_names())
                             - set(ResearchBase.metadata.tables))
        except DBAPIError:
            engine.dispose()
            raise ValueError(f"{path} is not a research store: it is not a SQLite "
                             "database.") from None
        if foreign:
            engine.dispose()
            raise ValueError(f"{path} is not a research store: it holds other tables "
                             f"({', '.join(foreign)}).")
        return cls(path, engine=engine)

    def __enter__(self) -> ResearchStore:
        """Return the store for use in a with block."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close the store."""
        self.close()

    @property
    def path(self) -> Path:
        """Return the research store's file path."""
        return self._path

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Yield a session whose work is committed together, or not at all."""
        with Session(self._engine, expire_on_commit=False) as session, session.begin():
            yield session

    @contextmanager
    def reader(self) -> Iterator[Session]:
        """Yield a session for reading."""
        with Session(self._engine, expire_on_commit=False) as session:
            yield session

    def table_names(self) -> list[str]:
        """Return the names of the tables in the file."""
        return sorted(inspect(self._engine).get_table_names())

    def close(self) -> None:
        """Release the file."""
        self._engine.dispose()
