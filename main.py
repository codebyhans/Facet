from __future__ import annotations

import importlib
import logging
import sys
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pillow_heif
from alembic import command
from alembic.config import Config
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from sqlalchemy import Engine

from photo_app.app.main_window import MainWindow
from photo_app.config.settings import AppSettings, load_settings
from photo_app.config.theme import apply_theme
from photo_app.infrastructure.db import create_sqlite_engine
from photo_app.infrastructure.file_scanner import FileScanner
from photo_app.infrastructure.repositories import (
    SqlAlchemyAlbumRepository,
    SqlAlchemyFaceRepository,
    SqlAlchemyIdentityClusterRepository,
    SqlAlchemyImageRepository,
    SqlAlchemyPersonRepository,
    SqlAlchemySettingsRepository,
)
from photo_app.infrastructure.thumbnail_store import ThumbnailStore
from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
from photo_app.ml.clustering import AgeAwareClustering, ClusteringConfig
from photo_app.ml.embedding_model import (
    InsightFaceDetectorEmbeddingModel,
    OnnxEmbeddingModel,
)
from photo_app.ml.face_detector import InsightFaceDetector
from photo_app.services.album_query_cache_service import AlbumQueryCacheService
from photo_app.services.album_service import AlbumService
from photo_app.services.face_index_service import (
    FaceIndexDependencies,
    FaceIndexService,
)
from photo_app.services.face_review_service import FaceReviewService
from photo_app.services.identity_cluster_service import (
    TemporalIdentityClusterService,
    TemporalIdentityConfig,
)
from photo_app.services.identity_maintenance_jobs import IdentityMaintenanceJobs
from photo_app.services.image_index_service import ImageIndexService
from photo_app.services.metadata_sync_service import MetadataSyncService
from photo_app.services.person_service import PersonService
from photo_app.services.settings_service import RuntimeSettings, SettingsService
from photo_app.services.tags_service import TagService

if TYPE_CHECKING:
    from pathlib import Path

    from photo_app.domain.repositories import (
        FaceRepository,
        ImageRepository,
        PersonRepository,
    )
    from photo_app.ml.protocols import EmbeddingModel

pillow_heif.register_heif_opener()

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceContainer:
    """Runtime service graph."""

    image_index_service: ImageIndexService
    album_service: AlbumService
    person_service: PersonService
    face_index_service: FaceIndexService | None
    face_review_service: FaceReviewService
    metadata_sync_service: MetadataSyncService
    tag_service: TagService
    settings_service: SettingsService
    runtime_settings: RuntimeSettings
    thumbnail_tile_store: ThumbnailTileStore
    identity_cluster_service: TemporalIdentityClusterService
    identity_maintenance_jobs: IdentityMaintenanceJobs
    # Exposed so the composition root can kick off the deferred face-ML
    # initialization (see `_FaceMlInitWorker`) after the window is visible.
    image_repo: ImageRepository
    face_repo: FaceRepository
    person_repo: PersonRepository
    query_cache_service: AlbumQueryCacheService


def _db_url_from_path(db_path: Path) -> str:
    return f"sqlite+pysqlite:///{db_path}"


def configure_logging() -> None:
    """Configure process-wide logging for startup visibility."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_migrations(settings: AppSettings) -> None:
    """Apply Alembic migrations to the configured database."""
    LOGGER.info("Running Alembic migrations...")
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option(
        "sqlalchemy.url",
        _db_url_from_path(settings.db_path),
    )
    command.upgrade(alembic_config, "head")
    LOGGER.info("Alembic migrations complete.")


def build_services(settings: AppSettings, engine: Engine) -> ServiceContainer:
    """Create application services."""
    LOGGER.info("Building repositories and services...")
    image_repo = SqlAlchemyImageRepository(engine)
    face_repo = SqlAlchemyFaceRepository(engine)
    person_repo = SqlAlchemyPersonRepository(engine)
    identity_cluster_repo = SqlAlchemyIdentityClusterRepository(engine)
    album_repo = SqlAlchemyAlbumRepository(engine)
    settings_repo = SqlAlchemySettingsRepository(engine)

    settings_service = SettingsService(settings_repo, settings)
    runtime_settings = settings_service.get_runtime_settings()
    query_cache_service = AlbumQueryCacheService(engine, album_repo, image_repo)

    scanner = FileScanner()
    thumbnail_store = ThumbnailStore(
        settings.thumbnail_dir,
        max_size=runtime_settings.thumbnail_max_size,
    )
    thumbnail_tile_store = ThumbnailTileStore(
        engine,
        cache_directory=settings.cache_directory,
        tile_size=settings.tile_size,
        thumbnail_size=settings.thumbnail_size,
        images_per_tile=settings.images_per_tile,
    )
    image_index_service = ImageIndexService(
        image_repo,
        scanner,
        thumbnail_store,
        query_cache_service=query_cache_service,
    )
    album_service = AlbumService(
        album_repo,
        image_repo,
        query_cache_service=query_cache_service,
    )
    person_service = PersonService(person_repo)
    identity_cluster_service = TemporalIdentityClusterService(
        face_repository=face_repo,
        image_repository=image_repo,
        person_repository=person_repo,
        cluster_repository=identity_cluster_repo,
        config=TemporalIdentityConfig(
            match_threshold=runtime_settings.identity_match_threshold,
            merge_threshold=runtime_settings.identity_merge_threshold,
            variance_review_threshold=runtime_settings.identity_variance_review_threshold,
            recency_weight=runtime_settings.identity_recency_weight,
        ),
    )
    identity_maintenance_jobs = IdentityMaintenanceJobs(identity_cluster_service)
    face_review_service = FaceReviewService(
        image_repo,
        face_repo,
        person_repo,
        query_cache_service=query_cache_service,
        identity_cluster_service=identity_cluster_service,
    )

    # Face ML stack (InsightFace model load, several seconds) is initialized
    # separately, after the window is already visible — see
    # `_FaceMlInitWorker` / `_start_face_ml_init_async`. Building it here
    # synchronously used to block the very first window paint on it.

    # Create metadata and tags services
    metadata_sync_service = MetadataSyncService(engine)
    tag_service = TagService(engine)
    LOGGER.info("Service graph ready.")
    return ServiceContainer(
        image_index_service=image_index_service,
        album_service=album_service,
        person_service=person_service,
        face_index_service=None,
        face_review_service=face_review_service,
        metadata_sync_service=metadata_sync_service,
        tag_service=tag_service,
        settings_service=settings_service,
        runtime_settings=runtime_settings,
        thumbnail_tile_store=thumbnail_tile_store,
        identity_cluster_service=identity_cluster_service,
        identity_maintenance_jobs=identity_maintenance_jobs,
        image_repo=image_repo,
        face_repo=face_repo,
        person_repo=person_repo,
        query_cache_service=query_cache_service,
    )


def _init_face_ml_stack(  # noqa: PLR0913
    *,
    runtime_settings: RuntimeSettings,
    image_repo: ImageRepository,
    face_repo: FaceRepository,
    person_repo: PersonRepository,
    query_cache_service: AlbumQueryCacheService,
    identity_cluster_service: TemporalIdentityClusterService,
    identity_maintenance_jobs: IdentityMaintenanceJobs,
) -> FaceIndexService | None:
    """Initialize InsightFace and build the face indexing service.

    `FaceAnalysis(...).prepare()` loads several ONNX models from disk and
    commonly takes multiple seconds — safe to call off the main thread since
    nothing here touches Qt objects, only ML/repository objects (the same
    reasoning workers elsewhere in this app use for PIL image decoding).
    """
    try:
        insightface = importlib.import_module("insightface")

        LOGGER.info("Initializing InsightFace detector (default pipeline)...")
        # Only detection + recognition (embeddings) are used; skipping the
        # landmark and gender/age submodels saves work on every face.
        face_app = insightface.app.FaceAnalysis(
            allowed_modules=["detection", "recognition"],
            providers=["CPUExecutionProvider"],
        )
        face_app.prepare(
            ctx_id=0,
            det_thresh=runtime_settings.detector_confidence_threshold,
        )
        detector = InsightFaceDetector(face_app)
        embedding_model: EmbeddingModel = InsightFaceDetectorEmbeddingModel(detector)
        if (
            runtime_settings.onnx_model_path is not None
            and runtime_settings.onnx_model_path.exists()
        ):
            LOGGER.info(
                "Using custom ONNX embedding override: %s",
                runtime_settings.onnx_model_path,
            )
            embedding_model = OnnxEmbeddingModel(
                model_path=str(runtime_settings.onnx_model_path),
                input_name=runtime_settings.onnx_input_name,
            )
        else:
            LOGGER.info("Using default InsightFace embeddings.")

        clustering = AgeAwareClustering(
            ClusteringConfig(
                age_penalty_weight=runtime_settings.clustering_age_penalty_weight,
                penalty_year_scale=runtime_settings.clustering_penalty_year_scale,
                min_cluster_size=runtime_settings.clustering_min_cluster_size,
            )
        )
        face_index_service = FaceIndexService(
            FaceIndexDependencies(
                image_repository=image_repo,
                face_repository=face_repo,
                person_repository=person_repo,
                detector=detector,
                embedding_model=embedding_model,
                clustering=clustering,
                query_cache_service=query_cache_service,
                identity_cluster_service=identity_cluster_service,
                identity_maintenance_jobs=identity_maintenance_jobs,
            )
        )
        LOGGER.info("Face ML stack initialized.")
    except Exception:
        LOGGER.exception("Face ML initialization failed; disabling face indexing.")
        return None
    return face_index_service


class _FaceMlInitWorkerSignals(QObject):
    """Signals for `_FaceMlInitWorker`."""

    ready = Signal(object)  # FaceIndexService | None


class _FaceMlInitWorker(QRunnable):
    """Run `_init_face_ml_stack` on a thread-pool worker.

    Keeps the multi-second InsightFace model load off the path that gates
    `window.show()`: the window opens immediately and face indexing becomes
    available a few seconds later via `MainWindow.set_face_index_service`,
    instead of the whole app appearing to hang before showing anything.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        runtime_settings: RuntimeSettings,
        image_repo: ImageRepository,
        face_repo: FaceRepository,
        person_repo: PersonRepository,
        query_cache_service: AlbumQueryCacheService,
        identity_cluster_service: TemporalIdentityClusterService,
        identity_maintenance_jobs: IdentityMaintenanceJobs,
    ) -> None:
        super().__init__()
        self._runtime_settings = runtime_settings
        self._image_repo = image_repo
        self._face_repo = face_repo
        self._person_repo = person_repo
        self._query_cache_service = query_cache_service
        self._identity_cluster_service = identity_cluster_service
        self._identity_maintenance_jobs = identity_maintenance_jobs
        self.signals = _FaceMlInitWorkerSignals()

    @Slot()
    def run(self) -> None:
        """Run the worker in a background thread."""
        face_index_service = _init_face_ml_stack(
            runtime_settings=self._runtime_settings,
            image_repo=self._image_repo,
            face_repo=self._face_repo,
            person_repo=self._person_repo,
            query_cache_service=self._query_cache_service,
            identity_cluster_service=self._identity_cluster_service,
            identity_maintenance_jobs=self._identity_maintenance_jobs,
        )
        self.signals.ready.emit(face_index_service)


# Keeps background workers started from this module alive until they finish
# emitting (mirrors the `self._active_..._worker` pattern used by widgets
# elsewhere in this app, adapted for the composition root, which has no
# natural owning widget of its own for a startup-only worker).
_background_workers: list[QRunnable] = []


def _start_face_ml_init_async(services: ServiceContainer, window: MainWindow) -> None:
    """Kick off face-ML initialization in the background and wire its result to `window`."""
    worker = _FaceMlInitWorker(
        runtime_settings=services.runtime_settings,
        image_repo=services.image_repo,
        face_repo=services.face_repo,
        person_repo=services.person_repo,
        query_cache_service=services.query_cache_service,
        identity_cluster_service=services.identity_cluster_service,
        identity_maintenance_jobs=services.identity_maintenance_jobs,
    )

    def _on_ready(face_index_service: FaceIndexService | None) -> None:
        _background_workers.remove(worker)
        window.set_face_index_service(face_index_service)

    worker.signals.ready.connect(_on_ready)
    _background_workers.append(worker)
    QThreadPool.globalInstance().start(worker)


def build_main_window(settings: AppSettings, engine: Engine) -> MainWindow:
    """Create application object graph and return main window."""
    LOGGER.info("Creating main window...")
    services = build_services(settings, engine)
    _ = services.person_service
    try:
        window = MainWindow(
            services.image_index_service,
            services.album_service,
            services.face_index_service,
            services.face_review_service,
            services.metadata_sync_service,
            services.tag_service,
            services.settings_service,
            services.runtime_settings,
            services.thumbnail_tile_store,
            cache_directory=settings.cache_directory,
        )
    except Exception:
        traceback.print_exc()
        raise
    LOGGER.info("Main window created.")
    _start_face_ml_init_async(services, window)
    return window


def main() -> int:
    """Application entry point."""
    configure_logging()
    LOGGER.info("Application startup begin.")
    settings = load_settings()
    LOGGER.info("Settings loaded.")
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.thumbnail_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_directory.mkdir(parents=True, exist_ok=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Data directories ensured at: %s", settings.db_path.parent)

    run_migrations(settings)
    LOGGER.info("Creating SQLAlchemy engine...")
    engine = create_sqlite_engine(str(settings.db_path))
    LOGGER.info("SQLAlchemy engine ready.")

    LOGGER.info("Creating QApplication...")
    app = QApplication(sys.argv)
    apply_theme(app)
    LOGGER.info("Dark theme applied.")
    window = build_main_window(settings, engine)
    LOGGER.info("Showing UI window...")
    window.show()
    LOGGER.info("Qt event loop starting.")
    code = app.exec()
    LOGGER.info("Application shutdown with code %s.", code)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
