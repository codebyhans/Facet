from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

_DEFAULT_BATCH_SIZE: Final[int] = 128
_DEFAULT_AGE_PENALTY_WEIGHT: Final[float] = 0.15
_DEFAULT_AGE_PENALTY_SCALE: Final[float] = 10.0
_DEFAULT_MIN_CLUSTER_SIZE: Final[int] = 2
_DEFAULT_DETECTOR_THRESHOLD: Final[float] = 0.55
_DEFAULT_THUMBNAIL_MAX_SIZE: Final[int] = 300
_DEFAULT_THUMBNAIL_SIZE: Final[tuple[int, int]] = (128, 128)
_DEFAULT_TILE_SIZE: Final[tuple[int, int]] = (1024, 1024)
_DEFAULT_IMAGES_PER_TILE: Final[int] = 64
_DEFAULT_IDENTITY_MATCH_THRESHOLD: Final[float] = 0.52
_DEFAULT_IDENTITY_MERGE_THRESHOLD: Final[float] = 0.70
_DEFAULT_IDENTITY_VARIANCE_THRESHOLD: Final[float] = 0.35
_DEFAULT_IDENTITY_RECENCY_WEIGHT: Final[float] = 0.15
_DEFAULT_DETAIL_VIEW_ZOOM_MODE: Final[str] = "fit"
_DEFAULT_FACE_REVIEW_THRESHOLD: Final[int] = 3


@dataclass(frozen=True)
class AppSettings:
    """Application runtime settings."""

    db_path: Path
    thumbnail_dir: Path
    cache_directory: Path
    model_dir: Path
    default_photo_root_dir: Path
    face_batch_size: int = _DEFAULT_BATCH_SIZE
    clustering_age_penalty_weight: float = _DEFAULT_AGE_PENALTY_WEIGHT
    clustering_penalty_year_scale: float = _DEFAULT_AGE_PENALTY_SCALE
    clustering_min_cluster_size: int = _DEFAULT_MIN_CLUSTER_SIZE
    detector_confidence_threshold: float = _DEFAULT_DETECTOR_THRESHOLD
    thumbnail_max_size: int = _DEFAULT_THUMBNAIL_MAX_SIZE
    thumbnail_size: tuple[int, int] = _DEFAULT_THUMBNAIL_SIZE
    tile_size: tuple[int, int] = _DEFAULT_TILE_SIZE
    images_per_tile: int = _DEFAULT_IMAGES_PER_TILE
    identity_match_threshold: float = _DEFAULT_IDENTITY_MATCH_THRESHOLD
    identity_merge_threshold: float = _DEFAULT_IDENTITY_MERGE_THRESHOLD
    identity_variance_review_threshold: float = _DEFAULT_IDENTITY_VARIANCE_THRESHOLD
    identity_recency_weight: float = _DEFAULT_IDENTITY_RECENCY_WEIGHT
    detail_view_zoom_mode: str = _DEFAULT_DETAIL_VIEW_ZOOM_MODE
    face_review_threshold: int = _DEFAULT_FACE_REVIEW_THRESHOLD
    onnx_model_path: Path | None = None
    onnx_input_name: str = "input"


def load_settings() -> AppSettings:
    """Load settings using deterministic local defaults."""
    root = Path.cwd() / "photo_app_data"
    return AppSettings(
        db_path=root / "photo_manager.sqlite3",
        thumbnail_dir=root / "thumbnails",
        cache_directory=root / "cache",
        model_dir=root / "models",
        default_photo_root_dir=root / "photos",
    )
