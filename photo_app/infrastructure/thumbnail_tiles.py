from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from PIL import Image, ImageOps, UnidentifiedImageError

from photo_app.infrastructure.sqlalchemy_models import ImageModel, ThumbnailTileModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TileBuildResult:
    """Summary of a tile build operation."""

    images_built: int
    tiles_built: int


@dataclass(frozen=True)
class ImageTileLookup:
    """Tile lookup payload for one image."""

    image_id: int
    tile_index: int
    tile_path: Path
    position_in_tile: int
    x: int
    y: int
    width: int
    height: int


class ThumbnailTileBuilder:
    """Generate and persist deterministic thumbnail tiles."""

    def __init__(
        self,
        engine: Engine,
        *,
        cache_directory: Path,
        tile_size: tuple[int, int],
        thumbnail_size: tuple[int, int],
        images_per_tile: int,
    ) -> None:
        self._engine = engine
        self._cache_directory = cache_directory
        self._tile_dir = cache_directory / "tiles"
        self._tile_size = tile_size
        self._thumbnail_size = thumbnail_size
        self._images_per_tile = images_per_tile
        self._tile_dir.mkdir(parents=True, exist_ok=True)

    def build_missing_tiles(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TileBuildResult:
        """Append new tile files for images with no tile mapping.

        Commits mappings after each completed tile so progress is preserved
        if the process is interrupted.
        """
        with Session(self._engine) as session:
            tile_rows = self._load_images_without_tiles(session)
            if not tile_rows:
                return TileBuildResult(images_built=0, tiles_built=0)
            next_tile_index = self._next_tile_index(session)

        total = len(tile_rows)
        grid_width  = max(1, self._tile_size[0] // self._thumbnail_size[0])
        tile_canvas = self._new_tile_canvas()
        current_tile_path = self._tile_path(next_tile_index)
        pending_mappings: list[ThumbnailTileModel] = []
        tile_entries = 0
        tiles_built  = 0
        images_built = 0

        for image_id, file_path in tile_rows:
            thumb = self._load_thumbnail(Path(file_path))
            if thumb is None:
                if on_progress is not None:
                    on_progress(images_built + 1, total)
                continue

            position = tile_entries % self._images_per_tile
            col = position % grid_width
            row = position // grid_width
            tile_canvas.paste(thumb, (col * self._thumbnail_size[0],
                                       row * self._thumbnail_size[1]))

            pending_mappings.append(
                ThumbnailTileModel(
                    tile_path=str(current_tile_path),
                    tile_index=next_tile_index,
                    image_id=image_id,
                    position_in_tile=position,
                )
            )
            tile_entries += 1
            images_built += 1

            if on_progress is not None:
                on_progress(images_built, total)

            if tile_entries % self._images_per_tile == 0:
                # Tile is full — save PNG then commit mappings before moving on
                self._save_tile(tile_canvas, current_tile_path)
                tiles_built += 1
                self._flush_mappings(pending_mappings)
                pending_mappings = []
                next_tile_index += 1
                current_tile_path = self._tile_path(next_tile_index)
                tile_canvas = self._new_tile_canvas()
        # Save the final partial tile (if any)
        if pending_mappings:
            self._save_tile(tile_canvas, current_tile_path)
            tiles_built += 1
            self._flush_mappings(pending_mappings)

        return TileBuildResult(images_built=images_built, tiles_built=tiles_built)

    def _flush_mappings(self, mappings: list[ThumbnailTileModel]) -> None:
        """Persist tile mappings, ignoring duplicates from concurrent runs."""
        if not mappings:
            return
        
        # Count attempted inserts before filtering
        total_attempts = len(mappings)
        
        with Session(self._engine) as session:
            rows = [
                {
                    "tile_path": str(m.tile_path),
                    "tile_index": int(m.tile_index),
                    "image_id": int(m.image_id),
                    "position_in_tile": int(m.position_in_tile),
                }
                for m in mappings
            ]
            stmt = sqlite_insert(ThumbnailTileModel).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=['tile_index', 'position_in_tile'])
            session.execute(stmt)
            session.commit()
            
            # Log if duplicates were likely skipped (simple heuristic)
            # Since we can't reliably get rowcount from SQLite INSERT OR IGNORE,
            # we log a warning-level message when concurrent tile building might be happening
            if total_attempts > 0:
                logger.debug(f"Attempted to insert {total_attempts} tile mappings (duplicates silently ignored)")

    def rebuild_all_tiles(self) -> TileBuildResult:
        """Drop tile files and tile mapping table, then rebuild from all images."""
        # Remove both .png and .webp in case of legacy files
        for pattern in ("*.png", "*.webp"):
            for tile_file in self._tile_dir.glob(pattern):
                tile_file.unlink(missing_ok=True)
        with Session(self._engine) as session:
            session.execute(delete(ThumbnailTileModel))
            session.commit()
        return self.build_missing_tiles()

    def _load_images_without_tiles(self, session: Session) -> list[tuple[int, str]]:
        stmt = (
            select(ImageModel.id, ImageModel.file_path)
            .outerjoin(ThumbnailTileModel, ThumbnailTileModel.image_id == ImageModel.id)
            .where(ThumbnailTileModel.id.is_(None))
            .order_by(ImageModel.id.asc())
        )
        rows = session.execute(stmt).all()
        return [(int(row[0]), str(row[1])) for row in rows]

    def _next_tile_index(self, session: Session) -> int:
        max_value = session.scalar(select(func.max(ThumbnailTileModel.tile_index)))
        return int(max_value) + 1 if max_value is not None else 1

    def _new_tile_canvas(self) -> Image.Image:
        return Image.new("RGB", self._tile_size, color=(18, 18, 18))

    def _tile_path(self, tile_index: int) -> Path:
        return self._tile_dir / f"tile_{tile_index:06d}.png"

    def _save_tile(self, tile_canvas: Image.Image, tile_path: Path) -> None:
        tile_canvas.save(tile_path, format="PNG")

    def _load_thumbnail(self, source_path: Path) -> Image.Image | None:
        try:
            with Image.open(source_path) as source:
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                oriented.thumbnail(self._thumbnail_size, Image.Resampling.LANCZOS)
                thumb = Image.new("RGB", self._thumbnail_size, color=(18, 18, 18))
                x = (self._thumbnail_size[0] - oriented.width) // 2
                y = (self._thumbnail_size[1] - oriented.height) // 2
                thumb.paste(oriented, (x, y))
                return thumb
        except (OSError, UnidentifiedImageError):
            return None


class ThumbnailTileStore:
    """Read/maintain thumbnail tiles and image->tile lookups."""

    def __init__(
        self,
        engine: Engine,
        *,
        cache_directory: Path,
        tile_size: tuple[int, int],
        thumbnail_size: tuple[int, int],
        images_per_tile: int,
    ) -> None:
        self._engine = engine
        self._tile_size = tile_size
        self._thumbnail_size = thumbnail_size
        self._images_per_tile = images_per_tile
        self._builder = ThumbnailTileBuilder(
            engine,
            cache_directory=cache_directory,
            tile_size=tile_size,
            thumbnail_size=thumbnail_size,
            images_per_tile=images_per_tile,
        )

    def get_tile(self, tile_index: int) -> Path | None:
        """Return tile file path for one tile index."""
        with Session(self._engine) as session:
            stmt = (
                select(ThumbnailTileModel.tile_path)
                .where(ThumbnailTileModel.tile_index == tile_index)
                .limit(1)
            )
            tile_path = session.scalar(stmt)
            if tile_path is None:
                return None
            resolved = Path(str(tile_path))
            return resolved if resolved.exists() else None

    def get_image_tile(self, image_id: int) -> ImageTileLookup | None:
        """Return tile lookup details for one image id."""
        with Session(self._engine) as session:
            stmt = select(ThumbnailTileModel).where(ThumbnailTileModel.image_id == image_id)
            row = session.scalar(stmt)
            if row is None:
                return None
            grid_width = max(1, self._tile_size[0] // self._thumbnail_size[0])
            col = row.position_in_tile % grid_width
            line = row.position_in_tile // grid_width
            return ImageTileLookup(
                image_id=row.image_id,
                tile_index=row.tile_index,
                tile_path=Path(row.tile_path),
                position_in_tile=row.position_in_tile,
                x=col * self._thumbnail_size[0],
                y=line * self._thumbnail_size[1],
                width=self._thumbnail_size[0],
                height=self._thumbnail_size[1],
            )

    def get_image_tiles_batch(self, image_ids: list[int]) -> dict[int, ImageTileLookup]:
        """Return tile lookups for multiple image IDs in a single DB query."""
        if not image_ids:
            return {}
        with Session(self._engine) as session:
            stmt = select(ThumbnailTileModel).where(
                ThumbnailTileModel.image_id.in_(image_ids)
            )
            grid_width = max(1, self._tile_size[0] // self._thumbnail_size[0])
            result: dict[int, ImageTileLookup] = {}
            for row in session.scalars(stmt):
                col = row.position_in_tile % grid_width
                line = row.position_in_tile // grid_width
                result[row.image_id] = ImageTileLookup(
                    image_id=row.image_id,
                    tile_index=row.tile_index,
                    tile_path=Path(row.tile_path),
                    position_in_tile=row.position_in_tile,
                    x=col * self._thumbnail_size[0],
                    y=line * self._thumbnail_size[1],
                    width=self._thumbnail_size[0],
                    height=self._thumbnail_size[1],
                )
            return result

    def build_missing_tiles(
        self,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TileBuildResult:
        """Incrementally add tiles for unmapped images."""
        return self._builder.build_missing_tiles(on_progress=on_progress)

    def rebuild_all_tiles(self) -> TileBuildResult:
        """Rebuild entire tile cache from source image index."""
        return self._builder.rebuild_all_tiles()
