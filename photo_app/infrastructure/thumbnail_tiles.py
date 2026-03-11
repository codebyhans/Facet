from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from PIL import Image, ImageOps, UnidentifiedImageError

from photo_app.infrastructure.sqlalchemy_models import ImageModel, ThumbnailTileModel


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

    def build_missing_tiles(self) -> TileBuildResult:
        """Append new tile files for images with no tile mapping."""
        with Session(self._engine) as session:
            tile_rows = self._load_images_without_tiles(session)
            if not tile_rows:
                return TileBuildResult(images_built=0, tiles_built=0)

            grid_width = max(1, self._tile_size[0] // self._thumbnail_size[0])
            next_tile_index = self._next_tile_index(session)
            tile_canvas = self._new_tile_canvas()
            tile_entries = 0
            tiles_built = 0
            images_built = 0
            mappings: list[ThumbnailTileModel] = []
            current_tile_path = self._tile_path(next_tile_index)

            for image_id, file_path in tile_rows:
                thumb = self._load_thumbnail(Path(file_path))
                if thumb is None:
                    continue

                position = tile_entries % self._images_per_tile
                col = position % grid_width
                row = position // grid_width
                x = col * self._thumbnail_size[0]
                y = row * self._thumbnail_size[1]
                tile_canvas.paste(thumb, (x, y))

                mappings.append(
                    ThumbnailTileModel(
                        tile_path=str(current_tile_path),
                        tile_index=next_tile_index,
                        image_id=image_id,
                        position_in_tile=position,
                    )
                )
                tile_entries += 1
                images_built += 1

                if tile_entries % self._images_per_tile == 0:
                    self._save_tile(tile_canvas, current_tile_path)
                    tiles_built += 1
                    next_tile_index += 1
                    current_tile_path = self._tile_path(next_tile_index)
                    tile_canvas = self._new_tile_canvas()

            if tile_entries % self._images_per_tile != 0:
                self._save_tile(tile_canvas, current_tile_path)
                tiles_built += 1

            if mappings:
                session.bulk_save_objects(mappings)
                session.flush()
                session.commit()  # Ensure changes are persisted
            return TileBuildResult(images_built=images_built, tiles_built=tiles_built)

    def rebuild_all_tiles(self) -> TileBuildResult:
        """Drop tile files and tile mapping table, then rebuild from all images."""
        for tile_file in self._tile_dir.glob("*.webp"):
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
            print(f"[TILES] get_tile({tile_index}): path={resolved}, exists={resolved.exists()}")
            return resolved if resolved.exists() else None

    def get_image_tile(self, image_id: int) -> ImageTileLookup | None:
        """Return tile lookup details for one image id."""
        with Session(self._engine) as session:
            stmt = select(ThumbnailTileModel).where(ThumbnailTileModel.image_id == image_id)
            row = session.scalar(stmt)
            print(f"[TILES] get_image_tile({image_id}): {'found tile_index=' + str(row.tile_index) if row else 'NO ROW FOUND'}")
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

    def build_missing_tiles(self) -> TileBuildResult:
        """Incrementally add tiles for unmapped images."""
        return self._builder.build_missing_tiles()

    def rebuild_all_tiles(self) -> TileBuildResult:
        """Rebuild entire tile cache from source image index."""
        return self._builder.rebuild_all_tiles()
