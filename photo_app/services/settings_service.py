from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photo_app.config.settings import AppSettings
    from photo_app.domain.repositories import SettingsRepository


@dataclass(frozen=True)
class RuntimeSettings:
    """User-adjustable runtime settings persisted in SQLite."""

    photo_root_dir: Path
    face_batch_size: int
    clustering_age_penalty_weight: float
    clustering_penalty_year_scale: float
    clustering_min_cluster_size: int
    detector_confidence_threshold: float
    onnx_model_path: Path | None
    onnx_input_name: str
    thumbnail_max_size: int
    thumbnail_size: tuple[int, int]
    tile_size: tuple[int, int]
    images_per_tile: int
    identity_match_threshold: float
    identity_merge_threshold: float
    identity_variance_review_threshold: float
    identity_recency_weight: float
    face_review_threshold: int


class SettingsService:
    """Load and persist runtime settings through repository ports."""

    def __init__(
        self,
        repository: SettingsRepository,
        base_settings: AppSettings,
    ) -> None:
        self._repository = repository
        self._base = base_settings

    def get_runtime_settings(self) -> RuntimeSettings:
        """Return merged settings from defaults and persisted values."""
        raw = self._repository.get_all()

        default_root = str(self._base.default_photo_root_dir)
        photo_root = Path(raw.get("photo_root_dir", default_root))
        onnx_model_value = raw.get("onnx_model_path", "")
        onnx_model = Path(onnx_model_value) if onnx_model_value else None

        face_batch_size = self._as_int(
            raw.get("face_batch_size"),
            self._base.face_batch_size,
        )
        return RuntimeSettings(
            photo_root_dir=photo_root,
            face_batch_size=face_batch_size,
            clustering_age_penalty_weight=self._as_float(
                raw.get("clustering_age_penalty_weight"),
                self._base.clustering_age_penalty_weight,
            ),
            clustering_penalty_year_scale=self._as_float(
                raw.get("clustering_penalty_year_scale"),
                self._base.clustering_penalty_year_scale,
            ),
            clustering_min_cluster_size=self._as_int(
                raw.get("clustering_min_cluster_size"),
                self._base.clustering_min_cluster_size,
            ),
            detector_confidence_threshold=self._as_float(
                raw.get("detector_confidence_threshold"),
                self._base.detector_confidence_threshold,
            ),
            onnx_model_path=onnx_model,
            onnx_input_name=raw.get("onnx_input_name", self._base.onnx_input_name),
            thumbnail_max_size=self._as_int(
                raw.get("thumbnail_max_size"),
                self._base.thumbnail_max_size,
            ),
            thumbnail_size=self._base.thumbnail_size,
            tile_size=self._base.tile_size,
            images_per_tile=self._base.images_per_tile,
            identity_match_threshold=self._as_float(
                raw.get("identity_match_threshold"),
                self._base.identity_match_threshold,
            ),
            identity_merge_threshold=self._as_float(
                raw.get("identity_merge_threshold"),
                self._base.identity_merge_threshold,
            ),
            identity_variance_review_threshold=self._as_float(
                raw.get("identity_variance_review_threshold"),
                self._base.identity_variance_review_threshold,
            ),
            identity_recency_weight=self._as_float(
                raw.get("identity_recency_weight"),
                self._base.identity_recency_weight,
            ),
            face_review_threshold=self._as_int(
                raw.get("face_review_threshold"),
                self._base.face_review_threshold,
            ),
        )

    def save_runtime_settings(self, settings: RuntimeSettings) -> RuntimeSettings:
        """Validate and persist runtime settings."""
        normalized = RuntimeSettings(
            photo_root_dir=settings.photo_root_dir,
            face_batch_size=max(1, settings.face_batch_size),
            clustering_age_penalty_weight=max(
                0.0,
                settings.clustering_age_penalty_weight,
            ),
            clustering_penalty_year_scale=max(
                1e-6,
                settings.clustering_penalty_year_scale,
            ),
            clustering_min_cluster_size=max(2, settings.clustering_min_cluster_size),
            detector_confidence_threshold=min(
                1.0,
                max(0.0, settings.detector_confidence_threshold),
            ),
            onnx_model_path=settings.onnx_model_path,
            onnx_input_name=settings.onnx_input_name.strip() or "input",
            thumbnail_max_size=max(64, settings.thumbnail_max_size),
            thumbnail_size=settings.thumbnail_size,
            tile_size=settings.tile_size,
            images_per_tile=max(1, settings.images_per_tile),
            identity_match_threshold=min(
                1.0,
                max(0.0, settings.identity_match_threshold),
            ),
            identity_merge_threshold=min(
                1.0,
                max(0.0, settings.identity_merge_threshold),
            ),
            identity_variance_review_threshold=max(
                0.0,
                settings.identity_variance_review_threshold,
            ),
            identity_recency_weight=max(
                0.0,
                settings.identity_recency_weight,
            ),
            face_review_threshold=max(1, min(100, settings.face_review_threshold)),
        )

        payload: dict[str, str] = {
            "photo_root_dir": str(normalized.photo_root_dir),
            "face_batch_size": str(normalized.face_batch_size),
            "clustering_age_penalty_weight": str(
                normalized.clustering_age_penalty_weight
            ),
            "clustering_penalty_year_scale": str(
                normalized.clustering_penalty_year_scale
            ),
            "clustering_min_cluster_size": str(normalized.clustering_min_cluster_size),
            "detector_confidence_threshold": str(
                normalized.detector_confidence_threshold
            ),
            "onnx_model_path": (
                ""
                if normalized.onnx_model_path is None
                else str(normalized.onnx_model_path)
            ),
            "onnx_input_name": normalized.onnx_input_name,
            "thumbnail_max_size": str(normalized.thumbnail_max_size),
            "identity_match_threshold": str(normalized.identity_match_threshold),
            "identity_merge_threshold": str(normalized.identity_merge_threshold),
            "identity_variance_review_threshold": str(
                normalized.identity_variance_review_threshold
            ),
            "identity_recency_weight": str(normalized.identity_recency_weight),
            "face_review_threshold": str(normalized.face_review_threshold),
        }
        self._repository.upsert_many(payload)
        return normalized

    def _as_int(self, value: str | None, fallback: int) -> int:
        if value is None:
            return fallback
        try:
            return int(value)
        except ValueError:
            return fallback

    def _as_float(self, value: str | None, fallback: float) -> float:
        if value is None:
            return fallback
        try:
            return float(value)
        except ValueError:
            return fallback

    def get_face_review_threshold(self) -> int:
        """Get the minimum number of images required for a cluster to show in face review."""
        return self.get_runtime_settings().face_review_threshold

    def set_face_review_threshold(self, threshold: int) -> None:
        """Set the minimum number of images required for a cluster to show in face review."""
        current = self.get_runtime_settings()
        updated = RuntimeSettings(
            photo_root_dir=current.photo_root_dir,
            face_batch_size=current.face_batch_size,
            clustering_age_penalty_weight=current.clustering_age_penalty_weight,
            clustering_penalty_year_scale=current.clustering_penalty_year_scale,
            clustering_min_cluster_size=current.clustering_min_cluster_size,
            detector_confidence_threshold=current.detector_confidence_threshold,
            onnx_model_path=current.onnx_model_path,
            onnx_input_name=current.onnx_input_name,
            thumbnail_max_size=current.thumbnail_max_size,
            thumbnail_size=current.thumbnail_size,
            tile_size=current.tile_size,
            images_per_tile=current.images_per_tile,
            identity_match_threshold=current.identity_match_threshold,
            identity_merge_threshold=current.identity_merge_threshold,
            identity_variance_review_threshold=current.identity_variance_review_threshold,
            identity_recency_weight=current.identity_recency_weight,
            face_review_threshold=threshold,
        )
        self.save_runtime_settings(updated)
