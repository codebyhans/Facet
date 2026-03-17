"""Service for computing and storing photo quality scores."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from photo_app.infrastructure.sqlalchemy_models import ImageModel
from photo_app.ml.quality_scorer import QualityScorer

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class PhotoQualityService:
    """Compute and store photo quality scores in the database."""

    def __init__(self, engine: Engine) -> None:
        """Initialize with SQLAlchemy engine."""
        self._engine = engine

    def compute_quality_scores(self, image_ids: list[int] | None = None) -> None:
        """
        Compute quality scores for images and store in database.

        Args:
            image_ids: Specific image IDs to score. If None, scores all unscored images.
        """
        with Session(self._engine) as session:
            if image_ids is None:
                # Find all unscored images
                images = session.execute(
                    select(ImageModel).where(ImageModel.quality_score.is_(None))
                ).scalars()
            else:
                images = session.execute(
                    select(ImageModel).where(ImageModel.id.in_(image_ids))
                ).scalars()

            processed = 0
            for image in images:
                try:
                    score = QualityScorer.compute_quality_score(image.file_path)
                    image.quality_score = score
                    processed += 1

                    if processed % 100 == 0:
                        logger.info(
                            "Computed quality scores for %s images...", processed
                        )

                except Exception:
                    logger.exception("Failed to score %s", image.file_path)

            session.commit()
            logger.info("Completed quality scoring for %s images", processed)

    def compute_single_quality_score(self, image_id: int) -> float:
        """
        Compute and store quality score for a single image.

        Args:
            image_id: ID of image to score

        Returns:
            Quality score (0.0-1.0)
        """
        with Session(self._engine) as session:
            image = session.execute(
                select(ImageModel).where(ImageModel.id == image_id)
            ).scalar_one_or_none()

            if not image:
                logger.warning("Image %s not found", image_id)
                return 0.5

            try:
                score = QualityScorer.compute_quality_score(image.file_path)
            except Exception:
                logger.exception("Failed to score image %s", image_id)
                return 0.5
            else:
                image.quality_score = score
                session.commit()
                return score
