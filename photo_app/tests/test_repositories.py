from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from photo_app.domain.models import Image
from photo_app.infrastructure.db import init_db
from photo_app.infrastructure.repositories import SqlAlchemyImageRepository
from photo_app.infrastructure.sqlalchemy_models import Base

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_image_repository_roundtrip() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    init_db(engine)

    with Session(engine) as session:
        repo = SqlAlchemyImageRepository(session)
        repo.add_many(
            [
                Image(
                    id=None,
                    file_path="C:/photo.jpg",
                    capture_date=date(2024, 2, 3),
                    year=2024,
                    month=2,
                    hash="h1",
                    width=1200,
                    height=900,
                    indexed_at=INDEXED_AT,
                )
            ]
        )
        session.commit()

        assert repo.exists_by_path("C:/photo.jpg")
        all_images = repo.list_all()
        assert len(all_images) == 1
        assert all_images[0].hash == "h1"
