from __future__ import annotations

from datetime import UTC, datetime

from photo_app.domain.models import Album, Face, Image, Person
from photo_app.domain.value_objects import AlbumQuery, BoundingBox

TEST_YEAR = 2025
TEST_WIDTH = 3


def test_entities_store_expected_data() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    image = Image(
        id=1,
        file_path="C:/x.jpg",
        capture_date=datetime(TEST_YEAR, 1, 1, tzinfo=UTC),
        year=TEST_YEAR,
        month=1,
        hash="abc",
        width=100,
        height=200,
        indexed_at=now,
    )
    face = Face(
        id=1,
        image_id=1,
        bbox=BoundingBox(1, 2, TEST_WIDTH, 4),
        embedding=b"x",
        person_id=None,
    )
    person = Person(id=1, name="Jane", created_at=now, birth_date=None)
    album = Album(
        id=1,
        name="Family",
        query_definition=AlbumQuery(person_ids=(1,), date_from=None, date_to=None),
        query_version=1,
        created_at=now,
    )

    assert image.year == TEST_YEAR
    assert face.bbox.w == TEST_WIDTH
    assert person.name == "Jane"
    assert album.query_definition.person_ids == (1,)
