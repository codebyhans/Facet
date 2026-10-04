from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import pytest
from sqlalchemy import create_engine

from photo_app.domain.models import Face, Image, Person
from photo_app.domain.value_objects import BoundingBox
from photo_app.infrastructure.repositories import (
    SqlAlchemyFaceRepository,
    SqlAlchemyIdentityClusterRepository,
    SqlAlchemyImageRepository,
    SqlAlchemyPersonRepository,
)
from photo_app.infrastructure.sqlalchemy_models import Base
from photo_app.services.face_review_service import FaceReviewService
from photo_app.services.identity_cluster_service import TemporalIdentityClusterService

if TYPE_CHECKING:
    from pathlib import Path

INDEXED_AT = datetime(2026, 1, 1, tzinfo=UTC)
EXPECTED_PERSON_COUNT_AFTER_SPLIT = 2
EXPECTED_CLUSTER_COUNT = 2
EXPECTED_OUTLIER_COUNT = 2


def _embedding() -> bytes:
    return np.zeros(4, dtype=np.float32).tobytes()


def _make_service() -> tuple[
    FaceReviewService,
    SqlAlchemyImageRepository,
    SqlAlchemyFaceRepository,
    SqlAlchemyPersonRepository,
]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    service = FaceReviewService(image_repo, face_repo, person_repo)
    return service, image_repo, face_repo, person_repo


def test_cover_ignores_other_faces_and_picks_largest_bbox() -> None:
    service, image_repo, face_repo, person_repo = _make_service()

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/group.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="i1",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path="C:/solo.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="i2",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
        ]
    )
    images = {img.file_path: img for img in image_repo.list_all()}
    group_id = images["C:/group.jpg"].id
    solo_id = images["C:/solo.jpg"].id
    assert group_id is not None
    assert solo_id is not None

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    bob = person_repo.create(
        Person(id=None, name="Bob", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None
    assert bob.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=group_id,
                bbox=(group_bbox := BoundingBox(x=10, y=10, w=20, h=20)),
                embedding=_embedding(),
                person_id=alice.id,
            ),
            Face(
                id=None,
                image_id=group_id,
                bbox=BoundingBox(x=50, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=bob.id,
            ),
            Face(
                id=None,
                image_id=solo_id,
                bbox=(solo_bbox := BoundingBox(x=5, y=5, w=15, h=15)),
                embedding=_embedding(),
                person_id=alice.id,
            ),
        ]
    )

    stacks = {s.person_id: s for s in service.person_stacks()}
    alice_stack = stacks[alice.id]

    # Alice's face is larger in the group photo than in the solo one; other
    # people in the frame don't matter, only the size of her bbox does.
    assert alice_stack.cover_image_id == group_id
    assert alice_stack.cover_face_bbox == group_bbox


def test_cover_falls_back_to_group_photo_with_face_bbox() -> None:
    service, image_repo, face_repo, person_repo = _make_service()

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/only_group.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="i1",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image_id = image_repo.list_all()[0].id
    assert image_id is not None

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    bob = person_repo.create(
        Person(id=None, name="Bob", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None
    assert bob.id is not None

    alice_bbox = BoundingBox(x=10, y=10, w=20, h=20)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=alice_bbox,
                embedding=_embedding(),
                person_id=alice.id,
            ),
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(x=50, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=bob.id,
            ),
        ]
    )

    stacks = {s.person_id: s for s in service.person_stacks()}
    alice_stack = stacks[alice.id]

    # Only a group photo exists, so it's used as the cover, but the specific
    # face belonging to Alice must be surfaced so the UI can highlight it.
    assert alice_stack.cover_image_id == image_id
    assert alice_stack.cover_face_bbox == alice_bbox


def test_cover_prefers_image_with_larger_relative_face() -> None:
    service, image_repo, face_repo, person_repo = _make_service()

    # Two single-face-only candidates for Alice: one where her face is a tiny
    # fraction of a large frame, one where it's a big close-up. The close-up
    # should win so the highlighted face is actually legible in a small
    # preview thumbnail.
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/tiny_face.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="i1",
                width=1000,
                height=1000,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path="C:/big_face.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="i2",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
        ]
    )
    images = {img.file_path: img for img in image_repo.list_all()}
    tiny_id = images["C:/tiny_face.jpg"].id
    big_id = images["C:/big_face.jpg"].id
    assert tiny_id is not None
    assert big_id is not None

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=tiny_id,
                bbox=BoundingBox(x=10, y=10, w=20, h=20),  # 0.04% of frame
                embedding=_embedding(),
                person_id=alice.id,
            ),
            Face(
                id=None,
                image_id=big_id,
                bbox=(big_bbox := BoundingBox(x=5, y=5, w=80, h=80)),  # 64% of frame
                embedding=_embedding(),
                person_id=alice.id,
            ),
        ]
    )

    stacks = {s.person_id: s for s in service.person_stacks()}
    alice_stack = stacks[alice.id]

    assert alice_stack.cover_image_id == big_id
    assert alice_stack.cover_face_bbox == big_bbox


def test_cover_capture_date_matches_chosen_cover_image() -> None:
    service, image_repo, face_repo, person_repo = _make_service()

    cover_capture_date = datetime(2024, 3, 15, tzinfo=UTC)
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/solo.jpg",
                capture_date=cover_capture_date,
                year=2024,
                month=3,
                hash="i1",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image_id = image_repo.list_all()[0].id
    assert image_id is not None

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(x=10, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=alice.id,
            )
        ]
    )

    stacks = {s.person_id: s for s in service.person_stacks()}
    alice_stack = stacks[alice.id]

    # Surfaced so the UI can show it on hover, helping tell apart photos of
    # similar-looking people (e.g. babies) by when they were taken.
    # SQLite round-trips datetimes as naive, so compare on that basis.
    assert alice_stack.cover_capture_date == cover_capture_date.replace(tzinfo=None)


def test_cover_skips_files_missing_from_disk(tmp_path: Path) -> None:
    service, image_repo, face_repo, person_repo = _make_service()

    # Two single-face-only candidates for Alice, both real paths on disk —
    # except the higher-ranked (bigger relative face) one gets deleted before
    # we ask for the cover, simulating a file moved to trash and purged
    # without the DB record being cleaned up.
    deleted_path = tmp_path / "deleted.jpg"
    deleted_path.write_bytes(b"fake")
    kept_path = tmp_path / "kept.jpg"
    kept_path.write_bytes(b"fake")

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path=str(deleted_path),
                capture_date=None,
                year=None,
                month=None,
                hash="i1",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
            Image(
                id=None,
                file_path=str(kept_path),
                capture_date=None,
                year=None,
                month=None,
                hash="i2",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            ),
        ]
    )
    images = {img.file_path: img for img in image_repo.list_all()}
    deleted_id = images[str(deleted_path)].id
    kept_id = images[str(kept_path)].id
    assert deleted_id is not None
    assert kept_id is not None

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=deleted_id,
                bbox=BoundingBox(x=5, y=5, w=80, h=80),  # much bigger relative face
                embedding=_embedding(),
                person_id=alice.id,
            ),
            Face(
                id=None,
                image_id=kept_id,
                bbox=(kept_bbox := BoundingBox(x=10, y=10, w=20, h=20)),
                embedding=_embedding(),
                person_id=alice.id,
            ),
        ]
    )

    # Now delete the higher-ranked candidate's file, as if it had been
    # trashed and purged after indexing.
    deleted_path.unlink()

    stacks = {s.person_id: s for s in service.person_stacks()}
    alice_stack = stacks[alice.id]

    # Despite ranking worse on relative face size, the still-existing file
    # must win over the higher-ranked but now-missing one.
    assert alice_stack.cover_image_id == kept_id
    assert alice_stack.cover_face_bbox == kept_bbox


def test_images_for_person_returns_all_images_beyond_sample_limit() -> None:
    """The People tab grid caps sample_image_ids at sample_limit for
    performance, but the person detail view's "all images" gallery must
    show every image in the cluster, not just that capped preview.
    """
    service, image_repo, face_repo, person_repo = _make_service()

    total_images = 25
    sample_limit = 20
    image_repo.add_many(
        [
            Image(
                id=None,
                file_path=f"C:/img_{i}.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash=f"hash_{i}",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
            for i in range(total_images)
        ]
    )
    image_ids_by_path = {
        img.file_path: img.id for img in image_repo.list_all() if img.id is not None
    }

    alice = person_repo.create(
        Person(id=None, name="Alice", created_at=INDEXED_AT, birth_date=None)
    )
    assert alice.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_ids_by_path[f"C:/img_{i}.jpg"],
                bbox=BoundingBox(x=10, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=alice.id,
            )
            for i in range(total_images)
        ]
    )

    stacks = {s.person_id: s for s in service.person_stacks(sample_limit=sample_limit)}
    alice_stack = stacks[alice.id]
    assert alice_stack.image_count == total_images
    assert len(alice_stack.sample_image_ids) == sample_limit

    all_ids, all_paths, _ = service.images_for_person(alice.id)
    assert len(all_ids) == total_images
    assert len(all_paths) == total_images


def test_move_images_to_person_splits_selected_photos_only() -> None:
    """The split-cluster action must move only the selected images' faces,
    leaving the rest of the source person's photos untouched, and create
    the target person by name if they don't already exist.
    """
    service, image_repo, face_repo, person_repo = _make_service()

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path=f"C:/mixed_{i}.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash=f"mixed_{i}",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
            for i in range(3)
        ]
    )
    ids_by_path = {
        img.file_path: img.id for img in image_repo.list_all() if img.id is not None
    }
    image_ids = [ids_by_path[f"C:/mixed_{i}.jpg"] for i in range(3)]

    liam = person_repo.create(
        Person(id=None, name="Liam", created_at=INDEXED_AT, birth_date=None)
    )
    assert liam.id is not None

    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(x=10, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=liam.id,
            )
            for image_id in image_ids
        ]
    )

    # Split the last photo out to a brand new person, "Noah".
    moved = service.move_images_to_person([image_ids[-1]], liam.id, "Noah")
    assert moved == 1

    noah = person_repo.get_by_name("Noah")
    assert noah is not None
    assert noah.id is not None

    liam_ids, _, _ = service.images_for_person(liam.id)
    noah_ids, _, _ = service.images_for_person(noah.id)
    assert sorted(liam_ids) == sorted(image_ids[:-1])
    assert noah_ids == [image_ids[-1]]

    # Splitting to an existing person reuses that person rather than
    # creating a duplicate.
    moved_again = service.move_images_to_person([image_ids[0]], liam.id, "noah")
    assert moved_again == 1
    assert len(person_repo.list_all()) == EXPECTED_PERSON_COUNT_AFTER_SPLIT

    liam_ids_after, _, _ = service.images_for_person(liam.id)
    noah_ids_after, _, _ = service.images_for_person(noah.id)
    assert liam_ids_after == [image_ids[1]]
    assert sorted(noah_ids_after) == sorted([image_ids[0], image_ids[2]])


def test_rename_person_stack_rejects_existing_name() -> None:
    """Cosmetically renaming a tag to a name another, different person
    already has is refused — that's what `tag_cluster` is for instead
    (applying an *existing* tag to a cluster, never merging).
    """
    service, _image_repo, _face_repo, person_repo = _make_service()

    liam = person_repo.create(
        Person(id=None, name="Liam", created_at=INDEXED_AT, birth_date=None)
    )
    noah = person_repo.create(
        Person(id=None, name="Noah", created_at=INDEXED_AT, birth_date=None)
    )
    assert liam.id is not None
    assert noah.id is not None

    with pytest.raises(ValueError, match="already exists"):
        service.rename_person_stack(noah.id, "Liam")

    # Renaming to its own current name, or to a genuinely free name, is fine.
    service.rename_person_stack(liam.id, "Liam")
    service.rename_person_stack(noah.id, "Noah Jr")
    assert person_repo.get(noah.id).name == "Noah Jr"  # type: ignore[union-attr]


def test_merge_person_into_folds_mistaken_tag_into_existing_person() -> None:
    """A cluster accidentally tagged "Emma1" instead of "Emma" can be folded
    into "Emma": its faces and cluster move to Emma, Emma inherits the
    birth date she lacked, "Emma1" is deleted, and the two clusters stay
    independent (only the tag is shared).
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    cluster_repo = SqlAlchemyIdentityClusterRepository(engine)
    identity_service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    service = FaceReviewService(
        image_repo,
        face_repo,
        person_repo,
        identity_cluster_service=identity_service,
    )

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/emma.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="emma",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image_id = image_repo.list_all()[0].id
    assert image_id is not None

    cluster_a = cluster_repo.create_cluster(_embedding(), created_at=INDEXED_AT)
    cluster_b = cluster_repo.create_cluster(_embedding(), created_at=INDEXED_AT)
    assert cluster_a.id is not None
    assert cluster_b.id is not None
    emma_id = service.tag_cluster(cluster_a.id, "Emma")
    emma1_id = service.tag_cluster(cluster_b.id, "Emma1")
    assert emma_id is not None
    assert emma1_id is not None
    person_repo.update_birth_date(emma1_id, date(2015, 6, 1))

    # A face tagged "Emma1" outside any cluster must move too.
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image_id,
                bbox=BoundingBox(x=10, y=10, w=20, h=20),
                embedding=_embedding(),
                person_id=emma1_id,
            )
        ]
    )

    service.merge_person_into(emma1_id, emma_id)

    assert person_repo.get(emma1_id) is None
    emma = person_repo.get(emma_id)
    assert emma is not None
    assert emma.birth_date == date(2015, 6, 1)
    assert face_repo.list_by_person(emma1_id) == []
    assert len(face_repo.list_by_person(emma_id)) == 1
    refreshed_b = cluster_repo.get_cluster(cluster_b.id)
    assert refreshed_b is not None
    assert refreshed_b.person_id == emma_id
    assert len(cluster_repo.list_clusters()) == EXPECTED_CLUSTER_COUNT


def test_tag_cluster_resolves_existing_person_by_name_without_merging() -> None:
    """`tag_cluster` applied twice with the same name, to two different
    clusters, links both to one person without merging their cluster state
    — the FaceReviewService-level name-resolution wrapper around
    `TemporalIdentityClusterService.tag_cluster`.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    cluster_repo = SqlAlchemyIdentityClusterRepository(engine)
    identity_service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    service = FaceReviewService(
        image_repo,
        face_repo,
        person_repo,
        identity_cluster_service=identity_service,
    )

    cluster_a = cluster_repo.create_cluster(_embedding(), created_at=INDEXED_AT)
    cluster_b = cluster_repo.create_cluster(_embedding(), created_at=INDEXED_AT)
    assert cluster_a.id is not None
    assert cluster_b.id is not None

    first_id = service.tag_cluster(cluster_a.id, "Emma", date(2015, 6, 1))
    second_id = service.tag_cluster(cluster_b.id, "emma")  # case-insensitive match
    assert first_id is not None
    assert first_id == second_id

    emma = person_repo.get(first_id)
    assert emma is not None
    assert emma.birth_date == date(2015, 6, 1)

    refreshed_a = cluster_repo.get_cluster(cluster_a.id)
    refreshed_b = cluster_repo.get_cluster(cluster_b.id)
    assert refreshed_a is not None
    assert refreshed_b is not None
    assert refreshed_a.person_id == first_id
    assert refreshed_b.person_id == first_id
    # Still two independent clusters — tagging never merges.
    assert len(cluster_repo.list_clusters()) == EXPECTED_CLUSTER_COUNT


def test_exclude_people_is_fully_undoable_via_restore() -> None:
    """Bulk-excluding a person never deletes the underlying face detection
    (bbox/embedding) or photo — restoring it makes it visible and
    clusterable again, with the same bbox it always had.
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    cluster_repo = SqlAlchemyIdentityClusterRepository(engine)
    identity_service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=cluster_repo,
    )
    service = FaceReviewService(
        image_repo,
        face_repo,
        person_repo,
        identity_cluster_service=identity_service,
    )

    image_repo.add_many(
        [
            Image(
                id=None,
                file_path="C:/stranger.jpg",
                capture_date=None,
                year=None,
                month=None,
                hash="stranger",
                width=100,
                height=100,
                indexed_at=INDEXED_AT,
            )
        ]
    )
    image = image_repo.get_by_path("C:/stranger.jpg")
    assert image is not None
    assert image.id is not None

    stranger = person_repo.create(
        Person(id=None, name=None, created_at=INDEXED_AT, birth_date=None)
    )
    assert stranger.id is not None
    cluster = cluster_repo.create_cluster(_embedding(), created_at=INDEXED_AT)
    assert cluster.id is not None
    cluster_repo.set_cluster_person(cluster.id, stranger.id)

    original_bbox = BoundingBox(x=15, y=15, w=30, h=30)
    face_repo.add_many(
        [
            Face(
                id=None,
                image_id=image.id,
                bbox=original_bbox,
                embedding=_embedding(),
                person_id=stranger.id,
            )
        ]
    )
    face_id = face_repo.list_all_active()[0].id
    assert face_id is not None
    cluster_repo.upsert_membership(
        face_id=face_id, cluster_id=cluster.id, confidence=1.0, assigned_at=INDEXED_AT
    )

    excluded_count = service.exclude_people([stranger.id])
    assert excluded_count == 1

    excluded_face = face_repo.get(face_id)
    assert excluded_face is not None
    assert excluded_face.excluded is True
    assert excluded_face.person_id is None
    # The detection itself — bbox and embedding — is untouched.
    assert excluded_face.bbox == original_bbox

    items, total = service.list_excluded_faces()
    assert total == 1
    assert [item.face_id for item in items] == [face_id]
    assert items[0].bbox == original_bbox

    service.restore_faces([face_id])

    restored_face = face_repo.get(face_id)
    assert restored_face is not None
    assert restored_face.excluded is False
    assert restored_face.manual_assignment is False
    assert restored_face.bbox == original_bbox

    items_after, total_after = service.list_excluded_faces()
    assert total_after == 0
    assert items_after == []
