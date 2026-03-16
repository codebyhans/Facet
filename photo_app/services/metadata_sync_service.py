"""Service for synchronizing image metadata changes to files and database."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from photo_app.infrastructure.exif_handler import ExifMetadataHandler

logger = logging.getLogger(__name__)


class MetadataSyncService:
    """Synchronize metadata changes between database and image files (EXIF)."""

    def __init__(self, engine: Engine) -> None:
        """Initialize with SQLAlchemy engine."""
        self._engine = engine

    def sync_image_metadata(
        self,
        image_id: int,
        rating: int | None = None,
        tags: list[str] | None = None,
        user_notes: str | None = None,
    ) -> None:
        """
        Sync metadata changes for a single image to both DB and EXIF.

        Args:
            image_id: ID of image to update
            rating: New rating (1-5, or None to skip)
            tags: List of tag names to add (overwrites existing)
            user_notes: User notes/comments to set

        Side Effects:
            - Updates Image record in database
            - Writes rating and notes to EXIF file
            - Writes tags to sidecar/EXIF keywords
        """
        from photo_app.infrastructure.sqlalchemy_models import ImageModel, ImageTagModel

        with Session(self._engine) as session:
            # Fetch image from DB
            image = session.execute(
                select(ImageModel).where(ImageModel.id == image_id)
            ).scalar_one_or_none()

            if not image:
                logger.warning(f"Image {image_id} not found")
                return

            # Prepare metadata dict for EXIF write
            exif_metadata: dict = {}

            # Update and sync rating
            if rating is not None:
                if not (1 <= rating <= 5):
                    logger.warning(f"Invalid rating {rating}, skipping")
                else:
                    image.rating = rating
                    exif_metadata["rating"] = rating

            # Update and sync user notes
            if user_notes is not None:
                image.user_notes = user_notes
                exif_metadata["user_comment"] = user_notes

            # Update and sync tags
            if tags is not None:
                # Remove existing tags for this image
                existing_tags = session.execute(
                    select(ImageTagModel).where(ImageTagModel.image_id == image_id)
                ).scalars()
                for tag in existing_tags:
                    session.delete(tag)

                # Add new tags
                now = datetime.utcnow()
                for tag_name in tags:
                    tag = ImageTagModel(
                        image_id=image_id, tag_name=tag_name, created_at=now
                    )
                    session.add(tag)

                # Sync keywords to EXIF
                exif_metadata["keywords"] = tags

            # Write EXIF
            if exif_metadata:
                try:
                    ExifMetadataHandler.write_exif(image.file_path, exif_metadata)
                except Exception as e:
                    logger.error(f"Failed to write EXIF for {image.file_path}: {e}")

            # Update database
            image.updated_at = datetime.utcnow()
            session.commit()
            logger.info(
                f"Synced metadata for image {image_id}: rating={rating}, "
                f"tags={tags}, notes={user_notes}"
            )

    def batch_sync_metadata(
        self,
        image_ids: list[int],
        rating: int | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
    ) -> None:
        """
        Batch sync metadata to multiple images.

        Args:
            image_ids: List of image IDs to update
            rating: Rating to apply to all (1-5)
            add_tags: Tags to add to all images
            remove_tags: Tags to remove from all images
        """
        from photo_app.infrastructure.sqlalchemy_models import ImageModel, ImageTagModel

        with Session(self._engine) as session:
            images = session.execute(
                select(ImageModel).where(ImageModel.id.in_(image_ids))
            ).scalars()

            now = datetime.utcnow()
            processed = 0

            for image in images:
                if rating is not None and 1 <= rating <= 5:
                    image.rating = rating

                # Handle tags
                if add_tags or remove_tags:
                    # Get existing tags
                    existing_tags = session.execute(
                        select(ImageTagModel).where(ImageTagModel.image_id == image.id)
                    ).scalars()
                    existing_tag_names = {t.tag_name for t in existing_tags}

                    # Remove tags
                    if remove_tags:
                        for tag_name in remove_tags:
                            tag = session.execute(
                                select(ImageTagModel).where(
                                    (ImageTagModel.image_id == image.id)
                                    & (ImageTagModel.tag_name == tag_name)
                                )
                            ).scalar_one_or_none()
                            if tag:
                                session.delete(tag)
                                existing_tag_names.discard(tag_name)

                    # Add tags
                    if add_tags:
                        for tag_name in add_tags:
                            if tag_name not in existing_tag_names:
                                tag = ImageTagModel(
                                    image_id=image.id,
                                    tag_name=tag_name,
                                    created_at=now,
                                )
                                session.add(tag)

                image.updated_at = now
                processed += 1

            # Write EXIF for all
            exif_metadata = {}
            if rating is not None:
                exif_metadata["rating"] = rating
            if add_tags:
                exif_metadata["keywords"] = add_tags

            if exif_metadata:
                for image in images:
                    try:
                        ExifMetadataHandler.write_exif(image.file_path, exif_metadata)
                    except Exception as e:
                        logger.error(f"Failed to write EXIF for {image.file_path}: {e}")

            session.commit()
            logger.info(f"Batch synced {processed} images")

    def extract_and_store_metadata(self, image_id: int) -> None:
        """
        Read EXIF from file and update database with extracted metadata.

        Example: After indexing an image, read its existing EXIF data
        and populate the database fields.

        Args:
            image_id: Image ID to enrich with EXIF data
        """
        from photo_app.infrastructure.sqlalchemy_models import ImageModel

        with Session(self._engine) as session:
            image = session.execute(
                select(ImageModel).where(ImageModel.id == image_id)
            ).scalar_one_or_none()

            if not image:
                logger.warning(f"Image {image_id} not found")
                return

            try:
                exif_data = ExifMetadataHandler.read_exif(image.file_path)

                # Store camera model
                if exif_data["camera_model"]:
                    image.camera_model = exif_data["camera_model"]

                # Store GPS coordinates
                if exif_data["gps_latitude"]:
                    image.gps_latitude = exif_data["gps_latitude"]
                if exif_data["gps_longitude"]:
                    image.gps_longitude = exif_data["gps_longitude"]

                # Store user comment/notes
                if exif_data["user_comment"]:
                    image.user_notes = exif_data["user_comment"]

                session.commit()
                logger.info(f"Extracted EXIF metadata for image {image_id}")
            except Exception as e:
                logger.error(f"Failed to extract EXIF for {image.file_path}: {e}")
