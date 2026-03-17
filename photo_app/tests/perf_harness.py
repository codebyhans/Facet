from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from photo_app.domain.models import Image
from photo_app.infrastructure.repositories import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyImageRepository,
)
from photo_app.infrastructure.sqlalchemy_models import AlbumModel, Base, FaceModel
from photo_app.ml.clustering import AgeAwareClustering, ClusteringConfig
from photo_app.services.album_service import AlbumService

_DEFAULT_IMAGE_COUNT = 20_000
_DEFAULT_PERSON_COUNT = 1_000
_DEFAULT_EMBEDDINGS = 10_000
_DEFAULT_DIM = 512
_DEFAULT_PAGE_LIMIT = 200
_DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class PerfArgs:
    image_count: int
    person_count: int
    embeddings: int
    embedding_dim: int
    page_limit: int


@dataclass(frozen=True)
class TimingResult:
    name: str
    seconds: float


@dataclass(frozen=True)
class HarnessResult:
    timings: tuple[TimingResult, ...]


def parse_args() -> PerfArgs:
    """Parse CLI arguments for the local performance harness."""
    parser = argparse.ArgumentParser(description="Local performance harness")
    parser.add_argument("--images", type=int, default=_DEFAULT_IMAGE_COUNT)
    parser.add_argument("--persons", type=int, default=_DEFAULT_PERSON_COUNT)
    parser.add_argument("--embeddings", type=int, default=_DEFAULT_EMBEDDINGS)
    parser.add_argument("--embedding-dim", type=int, default=_DEFAULT_DIM)
    parser.add_argument("--page-limit", type=int, default=_DEFAULT_PAGE_LIMIT)
    parsed = parser.parse_args()
    return PerfArgs(
        image_count=parsed.images,
        person_count=parsed.persons,
        embeddings=parsed.embeddings,
        embedding_dim=parsed.embedding_dim,
        page_limit=parsed.page_limit,
    )


def run_harness(args: PerfArgs) -> HarnessResult:
    """Run indexing/query/clustering micro-benchmarks on synthetic data."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    image_repo = SqlAlchemyImageRepository(engine)
    album_repo = SqlAlchemyAlbumRepository(engine)
    album_service = AlbumService(album_repo, image_repo)
    timings: list[TimingResult] = []

    with Session(engine) as session:
        start = perf_counter()
        _seed_images_and_faces(
            session=session,
            image_repo=image_repo,
            image_count=args.image_count,
            person_count=args.person_count,
        )
        timings.append(TimingResult(name="seed_db", seconds=perf_counter() - start))

        album_id = _create_album(session, args.person_count)

        start = perf_counter()
        _ = album_service.list_album_images(album_id, offset=0, limit=args.page_limit)
        timings.append(
            TimingResult(name="album_query_page", seconds=perf_counter() - start)
        )

    start = perf_counter()
    _run_clustering(args.embeddings, args.embedding_dim)
    timings.append(
        TimingResult(
            name="cluster_embeddings",
            seconds=perf_counter() - start,
        )
    )

    return HarnessResult(timings=tuple(timings))


def _seed_images_and_faces(
    *,
    session: Session,
    image_repo: SqlAlchemyImageRepository,
    image_count: int,
    person_count: int,
) -> None:
    now = datetime.now(tz=UTC)

    images = [
        Image(
            id=None,
            file_path=f"C:/synthetic/{index:06d}.jpg",
            capture_date=datetime(2020 + (index % 5), 1, 1, tzinfo=UTC),
            year=2020 + (index % 5),
            month=1,
            hash=f"hash-{index:06d}",
            width=1920,
            height=1080,
            indexed_at=now,
        )
        for index in range(image_count)
    ]

    image_repo.add_many(images)
    session.flush()

    image_ids = [image.id for image in image_repo.list_all() if image.id is not None]
    for image_id in image_ids:
        session.add(
            FaceModel(
                image_id=image_id,
                bbox_x=0,
                bbox_y=0,
                bbox_w=100,
                bbox_h=100,
                embedding=np.zeros(512, dtype=np.float32).tobytes(),
                person_id=(image_id % person_count) + 1,
            )
        )
    session.commit()


def _create_album(session: Session, person_count: int) -> int:
    model = AlbumModel(
        name="Synthetic",
        query_definition={
            "person_ids": list(range(1, min(person_count, 20) + 1)),
            "date_from": "2020-01-01",
            "date_to": "2024-12-31",
        },
        created_at=datetime.now(tz=UTC),
    )
    session.add(model)
    session.commit()
    if model.id is None:
        msg = "Expected album ID to be assigned"
        raise ValueError(msg)
    return model.id


def _run_clustering(embedding_count: int, embedding_dim: int) -> None:
    rng = np.random.default_rng(42)
    embeddings = rng.standard_normal((embedding_count, embedding_dim), dtype=np.float32)
    dates = [date(2015 + ((index * 17) % 10), 1, 1) for index in range(embedding_count)]
    clustering = AgeAwareClustering(
        ClusteringConfig(
            age_penalty_weight=0.15,
            penalty_year_scale=10.0,
            min_cluster_size=2,
        )
    )
    _ = clustering.cluster(embeddings, dates)


def render_results(result: HarnessResult) -> None:
    """Print timing results in a stable plain-text format."""
    lines = ["Performance Harness Results\n"]
    lines.extend(
        [f"- {timing.name}: {timing.seconds:.4f}s\n" for timing in result.timings]
    )
    sys.stdout.write("".join(lines))


def main() -> int:
    args = parse_args()
    result = run_harness(args)
    render_results(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
