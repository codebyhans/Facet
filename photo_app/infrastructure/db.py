from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from photo_app.infrastructure.sqlalchemy_models import Base

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


def create_sqlite_engine(db_path: str) -> Engine:
    """Create a SQLite engine configured for concurrent background workers."""
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"timeout": 30},  # wait up to 30s before raising locked error
    )

    @event.listens_for(engine, "connect")
    def set_wal_mode(dbapi_connection: object, _connection_record: object) -> None:
        # Type ignore is needed because the exact type of dbapi_connection is database-specific
        # and not exposed in SQLAlchemy's public API
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, faster than FULL
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all known tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)


class SessionFactory:
    """Simple typed wrapper around SQLAlchemy sessionmaker."""

    def __init__(self, engine: Engine) -> None:
        self._maker = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    def session(self) -> Iterator[Session]:
        """Yield a managed DB session."""
        session = self._maker()
        try:
            yield session
        finally:
            session.close()
