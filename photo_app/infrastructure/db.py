from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from photo_app.infrastructure.sqlalchemy_models import Base

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


def create_sqlite_engine(db_path: str) -> Engine:
    """Create a SQLite engine for the local app database."""
    return create_engine(f"sqlite+pysqlite:///{db_path}", future=True)


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
