"""Service for managing image tags."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from photo_app.infrastructure.sqlalchemy_models import ImageTagModel

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


class TagService:
    """Manage user-defined tags for images."""

    def __init__(self, engine: Engine) -> None:
        """Initialize with SQLAlchemy engine."""
        self._engine = engine

    def add_tag(self, image_id: int, tag_name: str) -> None:
        """
        Add a tag to an image.

        Args:
            image_id: ID of image to tag
            tag_name: Tag name (case-insensitive, will be lowercased)
        """
        with Session(self._engine) as session:
            tag_name = tag_name.strip().lower()
            if not tag_name:
                return

            # Check if tag already exists
            existing = session.execute(
                select(ImageTagModel).where(
                    (ImageTagModel.image_id == image_id)
                    & (ImageTagModel.tag_name == tag_name)
                )
            ).scalar_one_or_none()

            if existing is not None:
                return  # Already tagged

            tag = ImageTagModel(
                image_id=image_id, tag_name=tag_name, created_at=datetime.now(tz=UTC)
            )
            session.add(tag)
            session.commit()
            logger.debug("Added tag '%s' to image %s", tag_name, image_id)

    def remove_tag(self, image_id: int, tag_name: str) -> None:
        """
        Remove a tag from an image.

        Args:
            image_id: ID of image
            tag_name: Tag name to remove
        """
        with Session(self._engine) as session:
            tag_name = tag_name.strip().lower()
            tag = session.execute(
                select(ImageTagModel).where(
                    (ImageTagModel.image_id == image_id)
                    & (ImageTagModel.tag_name == tag_name)
                )
            ).scalar_one_or_none()

            if tag:
                session.delete(tag)
                session.commit()
                logger.debug("Removed tag '%s' from image %s", tag_name, image_id)

    def get_image_tags(self, image_id: int) -> list[str]:
        """
        Get all tags for an image.

        Args:
            image_id: ID of image

        Returns:
            List of tag names
        """
        with Session(self._engine) as session:
            tags = session.execute(
                select(ImageTagModel.tag_name).where(ImageTagModel.image_id == image_id)
            ).scalars()

            return sorted(tags)

    def list_all_tags(self) -> list[str]:
        """
        Get all unique tags in the database.

        Returns:
            Sorted list of unique tag names
        """
        with Session(self._engine) as session:
            tags = session.execute(select(ImageTagModel.tag_name).distinct()).scalars()

            return sorted(tags)

    def get_tag_cloud(self) -> dict[str, int]:
        """
        Get tag popularity (tag name -> count).

        Returns:
            Dictionary mapping tag names to occurrence counts
        """
        with Session(self._engine) as session:
            results = session.execute(
                select(ImageTagModel.tag_name, func.count(ImageTagModel.id)).group_by(
                    ImageTagModel.tag_name
                )
            ).all()

            return {row[0]: row[1] for row in results}

    def batch_tag_images(self, image_ids: list[int], tag_names: list[str]) -> None:
        """
        Add multiple tags to multiple images.

        Args:
            image_ids: List of image IDs to tag
            tag_names: List of tag names to add
        """
        with Session(self._engine) as session:
            now = datetime.now(tz=UTC)
            added = 0

            for image_id in image_ids:
                for tag_name in tag_names:
                    normalized_tag = tag_name.strip().lower()
                    if not normalized_tag:
                        continue

                    # Check if already exists
                    existing = session.execute(
                        select(ImageTagModel).where(
                            (ImageTagModel.image_id == image_id)
                            & (ImageTagModel.tag_name == normalized_tag)
                        )
                    ).scalar_one_or_none()

                    if existing is None:
                        tag = ImageTagModel(
                            image_id=image_id,
                            tag_name=normalized_tag,
                            created_at=now,
                        )
                        session.add(tag)
                        added += 1

            session.commit()
            logger.info(
                "Batch tagged %s images with %s tags (%s new tags)",
                len(image_ids),
                len(tag_names),
                added,
            )

    def search_images_by_tag(self, tag_name: str) -> list[int]:
        """
        Find all images with a specific tag.

        Args:
            tag_name: Tag name to search for

        Returns:
            List of image IDs
        """
        with Session(self._engine) as session:
            tag_name = tag_name.strip().lower()
            image_ids = session.execute(
                select(ImageTagModel.image_id).where(ImageTagModel.tag_name == tag_name)
            ).scalars()

            return list(image_ids)

    def search_images_by_tags(
        self, tag_names: list[str], *, match_all: bool = False
    ) -> list[int]:
        """
        Find images matching tag criteria.

        Args:
            tag_names: List of tag names to search for
            match_all: If True, only return images with ALL tags.
                      If False, return images with ANY of the tags.

        Returns:
            List of image IDs
        """
        with Session(self._engine) as session:
            tag_names = [t.strip().lower() for t in tag_names if t.strip()]
            if not tag_names:
                return []

            if match_all:
                # Find images with all tags
                image_ids = session.execute(
                    select(ImageTagModel.image_id)
                    .where(ImageTagModel.tag_name.in_(tag_names))
                    .group_by(ImageTagModel.image_id)
                    .having(func.count(ImageTagModel.id) == len(tag_names))
                ).scalars()
            else:
                # Find images with any tag
                image_ids = session.execute(
                    select(ImageTagModel.image_id).where(
                        ImageTagModel.tag_name.in_(tag_names)
                    )
                ).scalars()

            return list(set(image_ids))
