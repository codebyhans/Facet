from __future__ import annotations

import contextlib
import dataclasses
import importlib
import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Q_ARG,
    QItemSelectionModel,
    QMetaObject,
    QModelIndex,
    QSize,
    Qt,
    QThreadPool,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode
from photo_app.app.models.photo_grid_model import PhotoGridModel, is_video_path
from photo_app.app.view_models.album_view_model import AlbumViewModel
from photo_app.app.view_models.gallery_view_model import GalleryViewModel
from photo_app.app.widgets.advanced_filter_editor import AdvancedFilterEditorDialog
from photo_app.app.widgets.album_tree import AlbumTreeWidget
from photo_app.app.widgets.chromecast_slideshow_dialog import ChromecastSlideshowDialog
from photo_app.app.widgets.collection_list import CollectionListWidget
from photo_app.app.widgets.excluded_faces_dialog import ExcludedFacesDialog
from photo_app.app.widgets.filter_bar import FilterBarWidget
from photo_app.app.widgets.image_detail_panel import ImageDetailPanel
from photo_app.app.widgets.import_dialog import ImportDialog
from photo_app.app.widgets.iphone_import_dialog import IPhoneImportDialog
from photo_app.app.widgets.metadata_editor import MetadataEditorPanel
from photo_app.app.widgets.people_browser import PeopleBrowser
from photo_app.app.widgets.photo_grid import PhotoGridWidget
from photo_app.app.widgets.slideshow_window import (
    DEFAULT_SECONDS,
    MAX_SECONDS,
    MIN_SECONDS,
    SlideshowItem,
    SlideshowWindow,
)
from photo_app.app.widgets.timeline_ruler import TimelineRuler
from photo_app.app.workers.album_worker import AlbumQueryWorker
from photo_app.app.workers.indexing_worker import IndexWorker
from photo_app.app.workers.people_list_worker import PeopleListWorker
from photo_app.app.workers.runner import start_worker
from photo_app.config.constants import DEFAULT_PAGE_SIZE
from photo_app.config.keyboard_shortcuts import get_shortcuts
from photo_app.services.face_index_service import FaceIndexResult
from photo_app.services.face_review_service import PersonStackSummary

if TYPE_CHECKING:
    from collections.abc import Callable

    from photo_app.app.models.photo_grid_model import PhotoGridItem
    from photo_app.domain.models import Collection, Image
    from photo_app.domain.repositories import ImageRepository
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.album_service import AlbumService
    from photo_app.services.chromecast_service import ChromecastService
    from photo_app.services.collection_service import CollectionService
    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.face_review_service import FaceReviewService
    from photo_app.services.image_index_service import ImageIndexService
    from photo_app.services.metadata_sync_service import MetadataSyncService
    from photo_app.services.settings_service import RuntimeSettings, SettingsService
    from photo_app.services.tags_service import TagService

LOGGER = logging.getLogger(__name__)
WINDOW_SIZE_VALUES = 2
SPLITTER_SIZE_VALUES = 3


def _ids_matching_face_mode(
    image_repo: ImageRepository, image_ids: list[int], face_mode: str
) -> set[int]:
    """Ids whose named-people presence matches face_mode ("with"/"without").

    Photos not face-indexed yet match neither option.
    """
    presence = image_repo.named_face_presence(image_ids)
    wanted = face_mode == "with"
    return {image_id for image_id in image_ids if presence.get(image_id) is wanted}


def _count_slideshow_photos(
    image_repo: ImageRepository,
    image_ids: list[int],
    *,
    skip_flagged: bool,
    face_mode: str,
) -> int:
    """How many of image_ids a slideshow with these options would show.

    Repository calls only, so it can run on a worker thread.
    """
    candidates = [
        image.id
        for image in image_repo.list_by_ids(image_ids)
        if image.id is not None
        and not is_video_path(image.file_path)
        and not (skip_flagged and image.flag is not None)
    ]
    if face_mode == "all":
        return len(candidates)
    return len(_ids_matching_face_mode(image_repo, candidates, face_mode))


class CreateFolderDialog(QDialog):
    """Simple folder creation dialog."""

    def __init__(
        self,
        default_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Folder")
        self._name = QLineEdit(default_name, self)
        self._name.setPlaceholderText("Folder name")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Name:", self._name)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def name(self) -> str:
        """Return name input."""
        return self._name.text().strip()


class MainWindow(QMainWindow):
    """3-panel layout: Albums (left) | Photos (center) | Metadata (right)."""

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        image_index_service: ImageIndexService,
        album_service: AlbumService,
        face_index_service: FaceIndexService | None,
        face_review_service: FaceReviewService,
        metadata_sync_service: MetadataSyncService,
        tag_service: TagService,
        collection_service: CollectionService,
        _settings_service: SettingsService,
        runtime_settings: RuntimeSettings,
        thumbnail_tile_store: ThumbnailTileStore,
        cache_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self._settings_path = Path("config/settings.json")
        self._ui_state = self._load_ui_state()
        self._image_index_service = image_index_service
        self._album_service = album_service
        self._face_index_service = face_index_service
        self._face_review_service = face_review_service
        self._settings_service = _settings_service
        self._metadata_sync_service = metadata_sync_service
        self._tag_service = tag_service
        self._collection_service = collection_service
        self._runtime_settings = runtime_settings
        self._cache_directory = cache_directory
        self._thread_pool = QThreadPool.globalInstance()

        # In-flight people worker, so a refresh can drop its stale result
        self._active_people_worker: PeopleListWorker | None = None
        self._people_epoch: int = 0
        self._thumbnail_tile_store = thumbnail_tile_store

        self._album_vm = AlbumViewModel(
            album_service,
            face_review_service,
            self._settings_path,
        )
        self._album_gallery_vm = GalleryViewModel(
            self._album_vm,
            thumbnail_tile_store,
            page_size=DEFAULT_PAGE_SIZE,
        )

        thumb_size = tuple(
            getattr(self._runtime_settings, "thumbnail_size", (128, 128))
        )
        tile_size = tuple(getattr(self._runtime_settings, "tile_size", (1024, 1024)))
        photo_model_kwargs = {
            "thumbnail_size": (int(thumb_size[0]), int(thumb_size[1])),
            "tile_size": (int(tile_size[0]), int(tile_size[1])),
        }

        # Build album tree
        self._album_tree_model = AlbumTreeModel(self)
        self._album_tree = AlbumTreeWidget(self._album_tree_model)

        # Build photo grid
        self._album_photo_model = PhotoGridModel(parent=self, **photo_model_kwargs)
        self._album_photo_grid = PhotoGridWidget(self._album_photo_model)
        self._album_photo_grid.set_collection_sources(
            collection_service.list_collections,
            collection_service.collections_containing,
        )
        self._timeline_ruler = TimelineRuler(self._album_photo_grid)

        # Build image detail panel (for full image viewing)
        self._image_detail_panel = ImageDetailPanel(
            self,
            settings=self._runtime_settings,
            face_review_service=self._face_review_service,
        )

        # Build people browser
        self._people_browser = PeopleBrowser(
            face_review_service=self._face_review_service,
            tile_store=thumbnail_tile_store,
            parent=self,
            cache_directory=self._cache_directory,
        )

        # Create persistent navigation header that's always visible
        self._nav_header = QWidget()
        nav_layout = QHBoxLayout(self._nav_header)
        nav_layout.setContentsMargins(12, 12, 12, 8)
        nav_layout.setSpacing(8)

        self._albums_btn = QPushButton("Albums")
        self._albums_btn.setCheckable(True)
        self._albums_btn.setChecked(True)
        self._albums_btn.setFixedHeight(32)
        self._albums_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d30;
                color: #ffffff;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:checked {
                background-color: #007acc;
                border-color: #007acc;
            }
            QPushButton:hover {
                background-color: #3e3e42;
            }
        """)
        self._albums_btn.clicked.connect(self._on_switch_to_albums)

        self._people_btn = QPushButton("People")
        self._people_btn.setCheckable(True)
        self._people_btn.setFixedHeight(32)
        self._people_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d30;
                color: #ffffff;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
            }
            QPushButton:checked {
                background-color: #007acc;
                border-color: #007acc;
            }
            QPushButton:hover {
                background-color: #3e3e42;
            }
        """)
        self._people_btn.clicked.connect(self._on_switch_to_people)

        self._cast_btn = QPushButton("Cast to TV")
        self._cast_btn.setFixedHeight(32)
        self._cast_btn.setEnabled(False)
        self._cast_btn.setToolTip(
            "Cast the current album as a slideshow to a Chromecast TV"
        )
        self._cast_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d30;
                color: #ffffff;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:enabled:hover {
                background-color: #3e3e42;
            }
            QPushButton:disabled {
                color: #666666;
            }
        """)
        self._cast_btn.clicked.connect(self._on_cast_to_tv)
        self._chromecast_service: ChromecastService | None = (
            None  # created lazily on first cast
        )

        self._slideshow_btn = QPushButton("Slideshow")
        self._slideshow_btn.setFixedHeight(32)
        self._slideshow_btn.setToolTip(
            "Fullscreen slideshow of the current view (F5)\n"
            "Space pause/resume · ←/→ skip · ↑/↓ time per photo\n"
            "P/X/U flag · Backspace clear flag · A toggle next photo after flagging"
            " · Esc exit"
        )
        self._slideshow_btn.setStyleSheet(self._cast_btn.styleSheet())
        self._slideshow_btn.clicked.connect(self._on_start_slideshow)

        # Separate small button for the slideshow options menu.
        self._slideshow_options_btn = QToolButton()
        self._slideshow_options_btn.setText("▾")
        self._slideshow_options_btn.setFixedSize(28, 32)
        self._slideshow_options_btn.setToolTip("Slideshow options")
        self._slideshow_options_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self._slideshow_options_btn.setStyleSheet("""
            QToolButton {
                background-color: #2d2d30;
                color: #ffffff;
                border: 1px solid #3e3e42;
                border-radius: 4px;
                font-size: 18px;
                padding: 0px;
            }
            QToolButton:hover {
                background-color: #3e3e42;
            }
            QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
        """)
        slideshow_menu = QMenu(self._slideshow_options_btn)
        self._start_slideshow_action = slideshow_menu.addAction("Start Slideshow\tF5")
        self._start_slideshow_action.triggered.connect(self._on_start_slideshow)
        # Each opening recounts the photos the current settings would show.
        slideshow_menu.aboutToShow.connect(self._count_slideshow_photos)
        self._slideshow_count_request = 0
        slideshow_menu.addSeparator()
        self._seconds_action = slideshow_menu.addAction("")
        self._seconds_action.triggered.connect(self._on_choose_slideshow_seconds)
        self._update_seconds_action()
        self._advance_on_flag_action = slideshow_menu.addAction(
            "Next Photo After Flagging (P/X/U)"
        )
        self._advance_on_flag_action.setCheckable(True)
        self._advance_on_flag_action.setChecked(
            self._ui_state.get("slideshow_advance_on_flag") is True
        )
        self._advance_on_flag_action.toggled.connect(
            self._on_slideshow_advance_on_flag_changed
        )
        self._newest_first_action = slideshow_menu.addAction(
            "Newest First (Backwards in Time)"
        )
        self._newest_first_action.setCheckable(True)
        self._newest_first_action.setChecked(
            self._ui_state.get("slideshow_newest_first") is True
        )
        self._newest_first_action.toggled.connect(
            self._on_slideshow_newest_first_changed
        )
        self._skip_flagged_action = slideshow_menu.addAction(
            "Skip Already-Flagged Photos"
        )
        self._skip_flagged_action.setCheckable(True)
        self._skip_flagged_action.setChecked(
            self._ui_state.get("slideshow_skip_flagged") is True
        )
        self._skip_flagged_action.toggled.connect(
            self._on_slideshow_skip_flagged_changed
        )
        # Only faces tagged with a named person count; strangers and
        # "Not of Interest" faces count as no face.
        faces_menu = slideshow_menu.addMenu("Named People")
        faces_group = QActionGroup(faces_menu)
        saved_face_mode = self._ui_state.get("slideshow_faces", "all")
        self._face_mode_actions: dict[str, QAction] = {}
        for mode, label in (
            ("all", "All Photos"),
            ("with", "Only Photos With Named People"),
            ("without", "Only Photos Without Named People"),
        ):
            action = faces_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode == saved_face_mode)
            action.setActionGroup(faces_group)
            action.triggered.connect(
                lambda _checked=False, m=mode: self._on_slideshow_face_mode_changed(m)
            )
            self._face_mode_actions[mode] = action
        if not faces_group.checkedAction():
            self._face_mode_actions["all"].setChecked(True)
        self._slideshow_options_btn.setMenu(slideshow_menu)
        self._slideshow: SlideshowWindow | None = None
        # True when the slideshow runs over the album gallery, which can
        # page in more photos; False for a person's (complete) gallery.
        self._slideshow_from_gallery = False
        # Newest-first needs the whole view: set while it is being loaded.
        self._slideshow_waiting_for_all = False

        nav_layout.addWidget(self._albums_btn)
        nav_layout.addWidget(self._people_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self._slideshow_btn)
        nav_layout.addWidget(self._slideshow_options_btn)
        nav_layout.addSpacing(8)
        nav_layout.addWidget(self._cast_btn)

        # Create left panel for albums tree only (no people browser)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Albums on top, hand-picked collections below
        self._collection_list = CollectionListWidget()
        albums_and_collections = QSplitter(Qt.Orientation.Vertical)
        albums_and_collections.addWidget(self._album_tree)
        albums_and_collections.addWidget(self._collection_list)
        albums_and_collections.setStretchFactor(0, 3)
        albums_and_collections.setStretchFactor(1, 1)

        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(albums_and_collections)  # Index 0 - Albums only
        self._left_stack.setCurrentIndex(0)
        left_layout.addWidget(self._left_stack, 1)

        # Create filter bar
        self._filter_bar = FilterBarWidget()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)

        # Create stacked widget for center panel (gallery or detail view)
        center_stack = QStackedWidget()
        grid_page = QWidget()
        grid_layout = QHBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)
        grid_layout.addWidget(self._album_photo_grid, 1)
        grid_layout.addWidget(self._timeline_ruler)
        center_stack.addWidget(grid_page)  # Index 0
        center_stack.addWidget(self._image_detail_panel)  # Index 1
        center_stack.setCurrentIndex(0)  # Start with gallery
        self._center_stack = center_stack

        # Build metadata editor (right panel)
        self._metadata_editor = MetadataEditorPanel(self)

        # Create center panel with filter bar and content
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.addWidget(self._filter_bar)  # Filter bar at top
        # Dated/Undated split, only shown while viewing the whole library
        self._date_tabs = QTabBar()
        self._date_tabs.addTab("Dated")
        self._date_tabs.addTab("Undated")
        self._date_tabs.setDrawBase(False)
        self._date_tabs.setVisible(False)
        self._date_tabs.currentChanged.connect(self._on_date_tab_changed)
        center_layout.addWidget(self._date_tabs)
        center_layout.addWidget(center_stack)  # Content below filter bar

        # Create 3-way splitter: albums (left) | photos/detail (center) | metadata (right)
        splitter = QSplitter()
        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)  # Use center panel instead of direct stack
        splitter.addWidget(self._metadata_editor)
        splitter.setStretchFactor(0, 1)  # Left: 1x space
        splitter.setStretchFactor(1, 3)  # Center: 3x space
        splitter.setStretchFactor(2, 1)  # Metadata: 1x space
        self._splitter = splitter
        self._viewing_mode = "gallery"  # Track current view mode: "gallery" or "detail"

        # Top-level mode switcher: Albums mode (splitter) vs People mode (full-width people browser)
        self._mode_stack = QStackedWidget()
        self._mode_stack.addWidget(splitter)  # Index 0 = Albums mode
        self._mode_stack.addWidget(self._people_browser)  # Index 1 = People mode
        self._mode_stack.setCurrentIndex(0)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._nav_header)  # Add persistent navigation header
        layout.addWidget(self._mode_stack)  # Use mode stack instead of direct splitter
        self.setCentralWidget(root)

        self._wire_signals()
        self._wire_timeline()
        self._wire_slideshow()
        self._wire_grid_selection()
        self._build_menu()
        self._setup_keyboard_shortcuts()

        # Add persistent left-side label for background task progress
        self._task_label = QLabel("")
        self._task_label.setStyleSheet("color: #aaaaaa; padding: 0 8px;")
        self.statusBar().insertPermanentWidget(0, self._task_label)

        self.statusBar().showMessage("Ready")

        # Initialise album tree and gallery on startup
        self._ensure_library_album()
        self._load_tree()
        self._refresh_collections()
        self._album_gallery_vm.select_library()
        # Populate the Albums filter bar's person dropdown cheaply, without
        # paying for the full People-tab stack computation (which scans
        # every face and image in the library) before the window can even
        # open. The People tab itself refreshes on first switch — see
        # `_on_switch_to_people()`.
        self._refresh_available_people_filter()
        self._restore_geometry()
        self.setWindowTitle("Facet - Photo browser")

    def set_face_index_service(
        self, face_index_service: FaceIndexService | None
    ) -> None:
        """Attach the face indexing service once it's ready.

        Face ML initialization (loading the InsightFace model) takes
        several seconds and runs on a background worker started from the
        composition root (`main.py`) after this window is already visible,
        rather than blocking construction/`show()` on it — see
        `_start_face_ml_init_async`. Until this is called, face-indexing
        actions report "unavailable" via the existing
        `self._face_index_service is None` checks, the same as when ML
        initialization fails outright.
        """
        self._face_index_service = face_index_service
        if face_index_service is not None:
            self.statusBar().showMessage("Face indexing ready", 5000)

    def _set_task_status(self, message: str) -> None:
        """Show a persistent background-task message (left side of status bar)."""
        self._task_label.setText(message)

    def _clear_task_status(self) -> None:
        """Clear the background-task message when work finishes."""
        self._task_label.setText("")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._save_ui_state()
        if self._chromecast_service is not None:
            self._chromecast_service.shutdown()
        super().closeEvent(event)

    def _wire_grid_selection(self) -> None:
        """Keep the info panel on the grid's current photo (click, arrow keys)."""
        self._album_photo_grid.currentPhotoChanged.connect(
            self._on_detail_image_selected
        )

    def _wire_timeline(self) -> None:
        """Date ruler beside the grid: dates per view, jumps into unloaded rows."""
        self._album_gallery_vm.timeline_ready.connect(self._timeline_ruler.set_dates)
        self._album_photo_grid.loadThroughRequested.connect(
            self._album_gallery_vm.load_through
        )

    def _wire_slideshow(self) -> None:
        """Feed newly paged-in gallery photos to a running slideshow."""
        self._album_gallery_vm.page_ready.connect(self._on_page_for_slideshow)

    def _wire_signals(self) -> None:
        self._album_vm.error.connect(self._show_error)
        self._album_gallery_vm.error.connect(self._show_error)
        self._album_gallery_vm.status.connect(self._on_gallery_status)
        self._album_gallery_vm.loading_started.connect(self._on_loading_started)
        self._album_gallery_vm.loading_finished.connect(self._on_loading_finished)
        self._album_gallery_vm.date_tabs_available.connect(self._date_tabs.setVisible)

        self._album_tree.albumSelected.connect(
            lambda album_id, qd: self._album_gallery_vm.select_album(
                album_id, query_definition=qd
            )
        )
        self._album_tree.albumSelected.connect(self._on_album_selected)
        self._album_tree.createFolderRequested.connect(self._on_create_folder)
        self._album_tree.createVirtualAlbumRequested.connect(
            self._on_create_virtual_album
        )
        self._album_tree.renameRequested.connect(self._on_rename_node)
        self._album_tree.deleteRequested.connect(self._on_delete_node)
        self._album_tree.editFiltersRequested.connect(self._on_edit_filters)
        self._album_tree.moveRequested.connect(self._on_move_node)

        self._collection_list.collectionSelected.connect(self._on_collection_selected)
        self._collection_list.createRequested.connect(self._on_create_collection)
        self._collection_list.renameRequested.connect(self._on_rename_collection)
        self._collection_list.deleteRequested.connect(self._on_delete_collection)
        self._collection_list.createAlbumRequested.connect(
            self._on_create_album_from_collection
        )
        self._album_photo_grid.addToCollectionRequested.connect(
            self._on_add_to_collection
        )
        self._album_photo_grid.removeFromCollectionRequested.connect(
            self._on_remove_from_collection
        )
        self._album_photo_grid.newCollectionRequested.connect(
            self._on_new_collection_from_photos
        )

        self._album_gallery_vm.page_ready.connect(
            lambda items, append: self._on_gallery_page(
                self._album_photo_model, items, append
            )
        )
        self._album_gallery_vm.page_ready.connect(
            lambda items, _append: self._cast_btn.setEnabled(bool(items))
        )
        self._album_gallery_vm.tile_ready.connect(
            lambda tile_index, pixmap: self._on_tile_ready(
                self._album_photo_model, tile_index, pixmap
            )
        )
        self._album_gallery_vm.status.connect(self._on_gallery_status)
        self._album_gallery_vm.error.connect(self._show_error)

        self._album_photo_model.tileRequested.connect(
            self._album_gallery_vm.request_tile
        )
        self._album_photo_model.loadMoreRequested.connect(
            self._album_gallery_vm.load_next_page
        )

        # Photo grid selection -> metadata editor update & detail panel
        self._album_photo_grid.photoActivated.connect(self._on_photo_selected)

        # Photo grid flag changes -> update database
        self._album_photo_grid.flagChanged.connect(self._on_flag_changed)

        # Photo grid permanent delete requests (disk + library)
        self._album_photo_grid.deletePhotosRequested.connect(
            self._on_delete_photos_requested
        )

        # Image detail panel signals
        self._image_detail_panel.closed.connect(self._on_detail_panel_closed)
        self._image_detail_panel.image_selected.connect(self._on_detail_image_selected)
        self._image_detail_panel.reindex_requested.connect(
            self._on_reindex_faces_requested
        )
        self._image_detail_panel.faces_changed.connect(self._on_face_names_changed)

        # Metadata editor signals -> sync service
        self._metadata_editor.rating_changed.connect(self._on_rating_changed)
        self._metadata_editor.tags_changed.connect(self._on_tags_changed)

        # People browser signals
        self._people_browser.person_selected.connect(self._on_person_selected)
        self._people_browser.back_to_stacks.connect(self._on_back_to_stacks)
        self._people_browser.person_renamed.connect(self._on_person_renamed)
        self._people_browser.cluster_tag_requested.connect(
            self._on_cluster_tag_requested
        )
        self._people_browser.clusters_tag_requested.connect(
            self._on_clusters_tag_requested
        )
        self._people_browser.person_birth_date_changed.connect(
            self._on_person_birth_date_changed
        )
        self._people_browser.people_exclude_requested.connect(
            self._on_people_exclude_requested
        )
        self._people_browser.show_unnamed_changed.connect(self._on_show_unnamed_changed)
        self._people_browser.face_delete_requested.connect(
            self._on_face_delete_requested
        )
        self._people_browser.face_reassign_requested.connect(
            self._on_face_reassign_requested
        )
        self._people_browser.faces_changed.connect(self._on_face_names_changed)
        self._people_browser.images_move_requested.connect(
            self._on_images_move_requested
        )

    def _build_menu(self) -> None:
        """Build application menu bar."""
        # File menu
        file_menu = self.menuBar().addMenu("&File")

        select_lib_action = file_menu.addAction("Select Library Folder...")
        select_lib_action.triggered.connect(self._on_select_library)

        file_menu.addSeparator()

        index_action = file_menu.addAction("Index Images")
        index_action.triggered.connect(self._run_image_index)

        face_action = file_menu.addAction("Index Faces")
        face_action.triggered.connect(self._run_face_index)

        fix_ts_action = file_menu.addAction("Fix Missing Timestamps")
        fix_ts_action.triggered.connect(self._run_backfill_timestamps)

        prune_action = file_menu.addAction("Remove Missing Files")
        prune_action.triggered.connect(self._run_prune_missing_files)

        dupes_action = file_menu.addAction("Find Duplicates…")
        dupes_action.triggered.connect(self._open_duplicate_dialog)

        file_menu.addSeparator()

        import_action = file_menu.addAction("Import from camera...")
        import_action.triggered.connect(self._open_import_dialog)

        iphone_action = file_menu.addAction("Import from iPhone...")
        iphone_action.triggered.connect(self._open_iphone_import_dialog)

        file_menu.addSeparator()

        create_album_action = file_menu.addAction("Create Album...")
        create_album_action.triggered.connect(self._on_file_create_album)

        file_menu.addSeparator()

        export_action = file_menu.addAction("Export Gallery as HTML...")
        export_action.triggered.connect(self._on_export_html_gallery)

        file_menu.addSeparator()

        exit_action = file_menu.addAction("E&xit")
        exit_action.triggered.connect(self.close)

        people_menu = self.menuBar().addMenu("&People")
        refresh_stacks_action = people_menu.addAction("Refresh Stacks")
        refresh_stacks_action.triggered.connect(self._refresh_people_list)

        people_menu.addSeparator()

        threshold_action = people_menu.addAction("Face Review Threshold...")
        threshold_action.triggered.connect(self._on_face_review_settings)

        people_menu.addSeparator()

        reset_identities_action = people_menu.addAction(
            "Reset All People && Re-cluster..."
        )
        reset_identities_action.triggered.connect(self._on_reset_identities_requested)

        people_menu.addSeparator()

        excluded_faces_action = people_menu.addAction("Excluded Faces...")
        excluded_faces_action.triggered.connect(self._on_excluded_faces_requested)

    def _setup_keyboard_shortcuts(self) -> None:
        """Set up application-wide keyboard shortcuts."""
        shortcuts = get_shortcuts()

        # Rating shortcuts (0-5)
        QShortcut(QKeySequence(shortcuts.RATING_0), self, self._on_shortcut_rating_0)
        QShortcut(QKeySequence(shortcuts.RATING_1), self, self._on_shortcut_rating_1)
        QShortcut(QKeySequence(shortcuts.RATING_2), self, self._on_shortcut_rating_2)
        QShortcut(QKeySequence(shortcuts.RATING_3), self, self._on_shortcut_rating_3)
        QShortcut(QKeySequence(shortcuts.RATING_4), self, self._on_shortcut_rating_4)
        QShortcut(QKeySequence(shortcuts.RATING_5), self, self._on_shortcut_rating_5)

        # Album operations
        QShortcut(QKeySequence(shortcuts.NEW_ALBUM), self, self._on_shortcut_new_album)
        QShortcut(
            QKeySequence(shortcuts.NEW_FOLDER), self, self._on_shortcut_new_folder
        )
        QShortcut(QKeySequence(shortcuts.RENAME), self, self._on_shortcut_rename)
        QShortcut(QKeySequence(shortcuts.DELETE), self, self._on_shortcut_delete)

        # Photo operations
        QShortcut(QKeySequence(shortcuts.ADD_TAG), self, self._on_shortcut_add_tag)
        QShortcut(
            QKeySequence(shortcuts.BATCH_FACES), self, self._on_shortcut_batch_faces
        )

        # Navigation shortcuts
        QShortcut(
            QKeySequence(shortcuts.NEXT_PHOTO), self, self._on_shortcut_next_photo
        )
        QShortcut(
            QKeySequence(shortcuts.PREV_PHOTO), self, self._on_shortcut_prev_photo
        )

        # Application
        QShortcut(QKeySequence(shortcuts.SLIDESHOW), self, self._on_start_slideshow)
        QShortcut(QKeySequence(shortcuts.INDEX_IMAGES), self, self._run_image_index)
        QShortcut(QKeySequence(shortcuts.INDEX_FACES), self, self._run_face_index)

    def _load_tree(self) -> None:
        nodes = self._album_vm.load_album_tree()
        self._album_tree_model.set_nodes(nodes)
        self._album_tree.expandAll()

        last_album_id = self._ui_state.get("last_opened_album")
        if isinstance(last_album_id, int):
            index = self._find_index_by_album_id(last_album_id)
            if index.isValid():
                self._album_tree.setCurrentIndex(index)
                self._album_gallery_vm.select_album(last_album_id)
        # Auto-select first album if none was previously opened
        elif nodes:
            self._album_tree.setCurrentIndex(self._album_tree_model.index(0, 0))
            first_node = nodes[0]
            if first_node.album_id is not None:
                self._album_gallery_vm.select_album(first_node.album_id)

    def _ensure_library_album(self) -> None:
        """Create a default 'Library' album if none exist."""
        albums = self._album_vm.list_albums()
        if albums:
            # Already have albums
            return

        # Create Library album that shows all images
        try:
            query_def: dict[str, object] = {}
            library_album = self._album_vm.create_virtual_album(
                "Library",
                None,
                query_def,
            )
            self._album_tree_model.add_node(library_album, None)
            self._persist_tree()
            self.statusBar().showMessage("Created 'Library' album for all images")
        except Exception as exc:
            LOGGER.exception("Could not create Library album")
            self.statusBar().showMessage(f"Could not create Library album: {exc}")

    def _reload_album_tree(self) -> None:
        """Reload album tree after indexing or other changes."""
        self.statusBar().showMessage("Reloading album tree...")
        self._load_tree()
        self.statusBar().showMessage("Album tree reloaded. Displaying photos...")

    def _refresh_album_counts(self, collection_id: int | None = None) -> None:
        """Recompute image count badges in place, keeping selection/expansion.

        With `collection_id`, only albums built on that collection are
        recounted, which keeps adding photos to a collection instant.
        """
        for node in self._flatten(self._album_tree_model.all_nodes()):
            if node.album_id is None:
                continue
            if collection_id is not None and not self._query_uses_collection(
                node.query_definition, collection_id
            ):
                continue
            count = self._album_vm.count_album_images(node.album_id)
            if count != node.image_count:
                self._album_tree_model.set_image_count(node.node_id, count)

    def _refresh_after_library_change(self) -> None:
        """Sync album counts and the open view after images were added/removed."""
        self._refresh_album_counts()
        self._album_gallery_vm.refresh_current()

    def _on_create_folder(self, parent_id: object) -> None:
        dialog = CreateFolderDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.name()
        if not name:
            return
        parent_node_id = parent_id if isinstance(parent_id, str) else None
        node = self._album_vm.create_folder(name, parent_node_id)
        self._album_tree_model.add_node(node, parent_node_id)
        self._persist_tree()

    def _on_create_virtual_album(
        self, parent_id: object, initial_query: dict[str, object] | None = None
    ) -> None:
        # Get available options for the advanced filter dialog
        all_persons = self._album_vm.list_filter_people()

        # Show advanced filter dialog
        dialog = AdvancedFilterEditorDialog(
            parent=self,
            available_persons=all_persons,
            current_query=initial_query,
            available_collections=self._list_collections(),
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Prompt for album name
        album_name, ok = QInputDialog.getText(
            self,
            "Create Virtual Album",
            "Album name:",
        )
        if not ok or not album_name.strip():
            return

        parent_node_id = parent_id if isinstance(parent_id, str) else None
        try:
            query_definition = dialog.get_query_definition()
            node = self._album_vm.create_virtual_album(
                album_name.strip(),
                parent_node_id,
                query_definition,
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._album_tree_model.add_node(node, parent_node_id)
        self._persist_tree()

    def _on_rename_node(self, node_id: str, new_name: str) -> None:
        index = self._album_tree_model.index_from_node_id(node_id)
        node = self._album_tree_model.node_from_index(index)
        if node is None:
            return
        try:
            resolved_name = self._album_vm.rename_node(node, new_name)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._album_tree_model.rename_node(node_id, resolved_name)
        self._persist_tree()

    def _on_delete_node(self, node_id: str) -> None:
        index = self._album_tree_model.index_from_node_id(node_id)
        node = self._album_tree_model.node_from_index(index)
        if node is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete",
            f"Delete '{node.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            for sub in self._flatten([node]):
                self._album_vm.delete_node(sub)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._album_tree_model.remove_node(node_id)
        self._persist_tree()

    def _on_edit_filters(self, node_id: str) -> None:
        index = self._album_tree_model.index_from_node_id(node_id)
        node = self._album_tree_model.node_from_index(index)
        if node is None or node.kind != "virtual":
            return

        # Get available options for the advanced filter dialog
        all_persons = self._album_vm.list_filter_people()

        # Show advanced filter dialog with current query loaded
        current_query = node.query_definition or {}
        dialog = AdvancedFilterEditorDialog(
            parent=self,
            available_persons=all_persons,
            current_query=current_query,
            available_collections=self._list_collections(),
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            query_definition = dialog.get_query_definition()
            node.query_definition = self._album_vm.update_album_query(
                node,
                query_definition,
            )
            if node.album_id is not None:
                self._album_tree_model.set_image_count(
                    node.node_id, self._album_vm.count_album_images(node.album_id)
                )
            self._persist_tree()
            # Immediately reload the grid if this album is currently displayed
            if (
                node.album_id is not None
                and self._album_gallery_vm.get_current_album_id() == node.album_id
            ):
                self._album_gallery_vm.select_album(
                    node.album_id,
                    query_definition=query_definition,
                )
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))

    def _on_move_node(
        self, node_ids: list[str], new_parent: object, position: object = None
    ) -> None:
        new_parent_id = new_parent if isinstance(new_parent, str) else None
        new_position = position if isinstance(position, int) else None
        moved = self._album_tree_model.move_nodes(node_ids, new_parent_id, new_position)
        if not moved:
            self._show_error("Cannot move album into its own subtree.")
            return
        for node_id in moved:
            self._album_vm.move_album(node_id, new_parent_id)
        self._persist_tree()
        self._reselect_nodes(moved)
        self.statusBar().showMessage(
            "Album moved" if len(moved) == 1 else f"{len(moved)} albums moved"
        )

    def _reselect_nodes(self, node_ids: list[str]) -> None:
        """Keep moved rows selected so they can be moved again."""
        selection = self._album_tree.selectionModel()
        selection.clearSelection()
        for node_id in node_ids:
            index = self._album_tree_model.index_from_node_id(node_id)
            if index.isValid():
                selection.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def _on_date_tab_changed(self, index: int) -> None:
        self._album_gallery_vm.set_date_tab(has_date=index == 0)

    def _on_album_selected(self, _album_id: int) -> None:
        """Handle album selection - no longer updates album label."""
        # Album selection is handled by the GalleryViewModel
        # The current album label has been removed as requested
        self._collection_list.clear_selection()

    def _list_collections(self) -> list[Collection]:
        try:
            return self._collection_service.list_collections()
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to load collections: {exc}")
            return []

    def _refresh_collections(self) -> None:
        """Reload the collections panel (names and image counts)."""
        self._collection_list.set_collections(self._list_collections())

    def _on_collection_selected(self, collection_id: int) -> None:
        self._album_tree.clearSelection()
        self._album_gallery_vm.select_collection(collection_id)

    def _prompt_new_collection(self) -> Collection | None:
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if not ok or not name.strip():
            return None
        try:
            collection = self._collection_service.create_collection(name)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return None
        self._refresh_collections()
        return collection

    def _on_create_collection(self) -> None:
        self._prompt_new_collection()

    def _on_new_collection_from_photos(self, image_ids: list[int]) -> None:
        collection = self._prompt_new_collection()
        if collection is not None and collection.id is not None:
            self._on_add_to_collection(collection.id, image_ids)

    def _on_add_to_collection(self, collection_id: int, image_ids: list[int]) -> None:
        try:
            added = self._collection_service.add_images(collection_id, image_ids)
            collection = self._collection_service.get_collection(collection_id)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to add to collection: {exc}")
            return
        self._refresh_collections()
        self._refresh_album_counts(collection_id)
        name = collection.name if collection is not None else "collection"
        message = f"Added {added} photo(s) to '{name}'"
        already = len(image_ids) - added
        if already:
            message += f" ({already} already in it)"
        self.statusBar().showMessage(message, 5000)

    def _on_remove_from_collection(
        self, collection_id: int, image_ids: list[int]
    ) -> None:
        try:
            removed = self._collection_service.remove_images(collection_id, image_ids)
            collection = self._collection_service.get_collection(collection_id)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to remove from collection: {exc}")
            return
        self._refresh_collections()
        self._refresh_album_counts(collection_id)
        # Removed photos should disappear from a view built on this collection
        if removed and self._current_view_uses_collection(collection_id):
            self._album_gallery_vm.refresh_current()
        name = collection.name if collection is not None else "collection"
        self.statusBar().showMessage(f"Removed {removed} photo(s) from '{name}'", 5000)

    def _current_view_uses_collection(self, collection_id: int) -> bool:
        if self._album_gallery_vm.get_current_collection_id() == collection_id:
            return True
        album_id = self._album_gallery_vm.get_current_album_id()
        if album_id is None:
            return False
        node = self._album_tree_model.node_from_index(
            self._find_index_by_album_id(album_id)
        )
        return node is not None and self._query_uses_collection(
            node.query_definition, collection_id
        )

    @staticmethod
    def _query_uses_collection(
        query: dict[str, object] | None, collection_id: int
    ) -> bool:
        for key in ("collection_ids", "exclude_collection_ids"):
            ids = (query or {}).get(key)
            if isinstance(ids, (list, tuple)) and collection_id in ids:
                return True
        return False

    def _on_rename_collection(self, collection_id: int, name: str) -> None:
        try:
            self._collection_service.rename_collection(collection_id, name)
        except Exception as exc:  # noqa: BLE001
            self._show_error(str(exc))
            return
        self._refresh_collections()

    def _on_delete_collection(self, collection_id: int) -> None:
        collection = self._collection_service.get_collection(collection_id)
        if collection is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Collection",
            f"Delete collection '{collection.name}'?\n\n"
            f"Its {collection.image_count} photo(s) stay in the library. "
            "Albums built on this collection will no longer match them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        was_open = self._album_gallery_vm.get_current_collection_id() == collection_id
        try:
            self._collection_service.delete_collection(collection_id)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to delete collection: {exc}")
            return
        self._refresh_collections()
        self._refresh_album_counts(collection_id)
        if was_open:
            self._album_gallery_vm.select_library()

    def _on_create_album_from_collection(self, collection_id: int) -> None:
        self._on_create_virtual_album(None, {"collection_ids": [collection_id]})

    def _on_gallery_page(
        self,
        model: PhotoGridModel,
        items: list[PhotoGridItem],
        append: bool,  # noqa: FBT001
    ) -> None:
        model.append_page(
            items,
            has_more=len(items) >= DEFAULT_PAGE_SIZE,
            append=append,
        )

    def _on_tile_ready(
        self,
        model: PhotoGridModel,
        tile_index: int,
        pixmap: object,
    ) -> None:
        if isinstance(pixmap, QPixmap):
            model.set_tile(tile_index, pixmap)

    def _on_photo_selected(self, image_id: int) -> None:
        """Handle photo selection to update metadata and show detail view."""
        try:
            # Find the image in the current model
            image_item: PhotoGridItem | None = None
            items = self._album_photo_model.items
            selected_index = 0

            for idx, item in enumerate(items):
                if item.image_id == image_id:
                    image_item = item
                    selected_index = idx
                    break

            if image_item:
                # Update metadata editor
                image = self._load_image_for_metadata(image_item.image_id)
                self._metadata_editor.set_image(image)

                # Switch to detail view
                self._switch_to_detail_view(items, selected_index)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to load image: {exc}")

    def _load_image_for_metadata(self, image_id: int) -> Image | None:
        """Load a full Image entity for metadata editing."""
        image_repo = self._album_vm.get_image_repository()
        if image_repo is None:
            return None
        return image_repo.get_by_id(image_id)

    def _switch_to_detail_view(
        self, items: list[PhotoGridItem], selected_index: int
    ) -> None:
        """Switch center panel from gallery to detail view."""
        if self._viewing_mode == "detail":
            # Already in detail view, just update
            self._image_detail_panel.load_image(items, selected_index)
            return

        # Switch stacked widget to detail panel (index 1)
        self._image_detail_panel.load_image(items, selected_index)
        self._center_stack.setCurrentIndex(1)
        self._viewing_mode = "detail"
        self.statusBar().showMessage("Detail View - Press ESC or click Back to Gallery")

    def _on_detail_panel_closed(self) -> None:
        """Return to gallery view from detail panel."""
        if self._viewing_mode != "detail":
            return

        # Switch stacked widget back to gallery (index 0)
        self._center_stack.setCurrentIndex(0)
        self._viewing_mode = "gallery"
        self.statusBar().showMessage("Back to Gallery View")

    def _on_detail_image_selected(self, image_id: int) -> None:
        """Update metadata when navigating images in detail view."""
        try:
            image = self._load_image_for_metadata(image_id)
            self._metadata_editor.set_image(image)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to update image info: {exc}")

    def _on_rating_changed(self, rating: int) -> None:
        """Handle rating change from metadata editor."""
        image = self._metadata_editor.get_current_image()
        if image is None:
            return
        try:
            # Sync rating to EXIF and database
            if image.id is None:
                self._show_error("Missing image ID for rating update")
                return
            self._metadata_sync_service.sync_image_metadata(
                image_id=image.id,
                rating=rating,
            )
            self.statusBar().showMessage(f"Rating saved: {rating}★")
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to save rating: {exc}")

    def _on_tags_changed(self, tags: list[str]) -> None:
        """Handle tags change from metadata editor."""
        image = self._metadata_editor.get_current_image()
        if image is None:
            return
        try:
            # Sync tags to EXIF and database
            if image.id is None:
                self._show_error("Missing image ID for tag update")
                return
            self._metadata_sync_service.sync_image_metadata(
                image_id=image.id,
                tags=tags,
            )
            self.statusBar().showMessage(f"Tags saved: {len(tags)} tags")
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to save tags: {exc}")

    def _on_switch_to_albums(self) -> None:
        """Switch to Albums view mode."""
        self._mode_stack.setCurrentIndex(0)
        self._albums_btn.setChecked(True)
        self._people_btn.setChecked(False)

    def _on_switch_to_people(self) -> None:
        """Switch to People clusters view mode."""
        self._mode_stack.setCurrentIndex(1)
        self._albums_btn.setChecked(False)
        self._people_btn.setChecked(True)
        self._refresh_people_list()

        # Ensure buttons remain visible and accessible
        self._albums_btn.setVisible(True)
        self._people_btn.setVisible(True)

    def _on_person_selected(self, person_id: int, stack: PersonStackSummary) -> None:
        """Handle person selection in people browser."""
        if isinstance(stack, PersonStackSummary):
            self._people_browser.show_person_detail(person_id, stack)
            self.statusBar().showMessage(
                f"Viewing: {stack.person_name or f'Cluster #{person_id}'}"
            )
        else:
            self.statusBar().showMessage(f"Selected person {person_id}")

    def _on_back_to_stacks(self) -> None:
        """Handle back to stacks button click."""
        self._refresh_people_list()

    def _on_person_renamed(self, person_id: int, name: str) -> None:
        """Handle a tag rename from people browser.

        Renaming to a name another person already has offers to merge this
        tag into that one (e.g. a cluster mistakenly tagged "A1" instead of
        "A") rather than failing.
        """
        existing = self._face_review_service.get_person_repository().get_by_name(
            name.strip()
        )
        if (
            existing is not None
            and existing.id is not None
            and existing.id != person_id
        ):
            self._merge_person_into(person_id, existing.id, existing.name or name)
            return
        try:
            self._face_review_service.rename_person_stack(person_id, name)
            self._refresh_people_list()
            self.statusBar().showMessage(f"Renamed person to '{name}'")
        except ValueError as exc:
            self._show_error(str(exc))
        except Exception as exc:
            LOGGER.exception("Failed to rename person")
            self._show_error(f"Failed to rename person: {exc}")

    def _merge_person_into(
        self, source_person_id: int, target_person_id: int, target_name: str
    ) -> None:
        """Confirm, then fold one person tag into another existing one."""
        source = self._face_review_service.get_person_repository().get(source_person_id)
        source_name = source.name if source is not None else "this person"
        reply = QMessageBox.question(
            self,
            "Merge People",
            f"'{target_name}' already exists.\n\n"
            f"Merge '{source_name}' into '{target_name}'? All of "
            f"'{source_name}''s photos will be tagged '{target_name}' and "
            f"'{source_name}' will be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._face_review_service.merge_person_into(
                source_person_id, target_person_id
            )
            self._refresh_people_list()
            self.statusBar().showMessage(f"Merged '{source_name}' into '{target_name}'")
        except Exception as exc:
            LOGGER.exception("Failed to merge people")
            self._show_error(f"Failed to merge people: {exc}")

    def _on_cluster_tag_requested(self, cluster_id: int, name: str) -> None:
        """Apply a name tag to an untagged cluster from people browser.

        Resolves an existing tag by name or creates a new one — never
        merges cluster state, so tagging several different clusters with
        the same name is just this action repeated on each one.
        """
        try:
            self._face_review_service.tag_cluster(cluster_id, name)
            self._refresh_people_list()
            self.statusBar().showMessage(f"Tagged as '{name}'")
        except Exception as exc:
            LOGGER.exception("Failed to tag cluster")
            self._show_error(f"Failed to tag cluster: {exc}")

    def _on_clusters_tag_requested(self, cluster_ids: list[int], name: str) -> None:
        """Apply one name tag to several selected clusters at once."""
        try:
            for cluster_id in cluster_ids:
                self._face_review_service.tag_cluster(cluster_id, name)
            self._refresh_people_list()
            self.statusBar().showMessage(
                f"Tagged {len(cluster_ids)} cluster(s) as '{name}'"
            )
        except Exception as exc:
            LOGGER.exception("Failed to tag clusters")
            self._show_error(f"Failed to tag clusters: {exc}")

    def _on_person_birth_date_changed(self, person_id: int, date_text: str) -> None:
        """Handle a birth date set (or cleared) from the people browser."""
        parsed: date | None = None
        if date_text:
            try:
                parsed = date.fromisoformat(date_text)
            except ValueError:
                self._show_error(f"Invalid birth date: {date_text!r}")
                return
        try:
            self._face_review_service.set_person_birth_date(person_id, parsed)
            self._refresh_people_list()
            label = parsed.isoformat() if parsed is not None else "cleared"
            self.statusBar().showMessage(f"Birth date {label}")
        except Exception as exc:
            LOGGER.exception("Failed to set birth date")
            self._show_error(f"Failed to set birth date: {exc}")

    def _on_people_exclude_requested(self, person_ids: list[int]) -> None:
        """Bulk-exclude selected people not of interest (e.g. strangers).

        Nothing is deleted: the underlying photo/bbox/embedding for every
        face is untouched. Only the `excluded` flag gets set (hiding them
        from the People tab and future clustering) and the now-empty
        cluster/person records are removed. Fully undoable any time via
        People → Excluded Faces... → Restore. Confirmed first since it
        affects everything currently labeled with the selected tags, not
        just one photo.
        """
        if not person_ids:
            return
        reply = QMessageBox.warning(
            self,
            "Mark Not of Interest",
            f"Exclude all faces for {len(person_ids)} selected person(s)? "
            "They'll no longer be clustered or shown in the People tab. "
            "Nothing is deleted — undo any time via "
            "People → Excluded Faces...",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            excluded = self._face_review_service.exclude_people(person_ids)
            self._refresh_people_list()
            self.statusBar().showMessage(f"Excluded {excluded} face(s)")
        except Exception as exc:
            LOGGER.exception("Failed to exclude selected people")
            self._show_error(f"Failed to exclude selected people: {exc}")

    def _on_show_unnamed_changed(self, show_unnamed: bool) -> None:  # noqa: FBT001
        """Handle show unnamed clusters toggle from people browser."""
        self._show_unnamed = show_unnamed
        self._refresh_people_list()

    def _on_face_delete_requested(self, face_id: int) -> None:
        """Remove a face detection from a cluster image.

        The inspector has already dropped the face from its overlay, so on
        success nothing is reloaded (that would interrupt face naming); the
        people list refresh waits for `_on_face_names_changed`.
        """
        try:
            self._face_review_service.remove_face(face_id)
            self.statusBar().showMessage("Face removed")
        except Exception as exc:  # noqa: BLE001
            self._people_browser.reload_current_inspector_image()
            self._show_error(f"Could not remove face: {exc}")

    def _on_face_reassign_requested(self, face_id: int, new_person_name: str) -> None:
        """Persist a face name set in the inspector (already shown there)."""
        try:
            self._face_review_service.assign_name(face_id, new_person_name)
            self.statusBar().showMessage(f"Face named '{new_person_name}'")
        except Exception as exc:  # noqa: BLE001
            self._people_browser.reload_current_inspector_image()
            self._show_error(f"Could not name face: {exc}")

    def _on_face_names_changed(self) -> None:
        """A face-naming session ended with edits: refresh people views once."""
        self._refresh_people_list()
        self._refresh_available_people_filter()

    def _on_images_move_requested(
        self, image_ids: list[int], source_person_id: int, target_person_name: str
    ) -> None:
        """Split selected photos out of the open person into a different one."""
        try:
            moved = self._face_review_service.move_images_to_person(
                image_ids, source_person_id, target_person_name
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Could not move photos: {exc}")
            return

        if moved == 0:
            self.statusBar().showMessage("No photos moved")
            return

        self.statusBar().showMessage(
            f"Moved {moved} photo(s) to '{target_person_name}'"
        )
        # Drop the moved photos from the open grid immediately instead of
        # re-fetching stacks (person_stacks() scans the whole library and
        # isn't cheap on a large one) — the person-list counts catch up on
        # the next natural refresh, same as after a merge today.
        if self._people_browser.get_current_person_id() == source_person_id:
            self._people_browser.remove_images_from_current_gallery(image_ids)
        self._refresh_people_list()

    def _refresh_available_people_filter(self) -> None:
        """Populate the Albums filter bar's person dropdown.

        Uses the cheap, direct persons-table query rather than waiting on
        `person_stacks()` (grouped via `_refresh_people_list()`), which also
        does a full faces/images library scan and per-person cover-image
        selection — far more work than needed just to list display names for
        a filter dropdown, and something the Albums view shouldn't have to
        wait on before it can filter by person.
        """
        try:
            all_people = self._face_review_service.get_available_people()
            named_people = [p for p in all_people if p.name]
            self._filter_bar.set_available_people(named_people)
        except Exception:
            LOGGER.exception("Failed to update available people filter")

    def _refresh_people_list(self) -> None:
        """Refresh the people clusters list in a background thread."""
        if self._settings_service is None:
            return

        # Cancel any in-flight worker by advancing the epoch and disconnecting its signal
        if self._active_people_worker is not None:
            with contextlib.suppress(RuntimeError):
                self._active_people_worker.signals.result_ready.disconnect()
            self._active_people_worker = None

        self._people_epoch += 1
        threshold = self._settings_service.get_face_review_threshold()
        show_unnamed = getattr(self, "_show_unnamed", False)

        worker = PeopleListWorker(
            self._face_review_service,
            tile_store=self._thumbnail_tile_store,
            cover_cache_dir=self._cache_directory,
            min_image_count=threshold,
            show_unnamed=show_unnamed,
            epoch=self._people_epoch,
        )
        worker.signals.result_ready.connect(self._on_people_list_ready)
        worker.signals.error.connect(
            lambda msg: self._show_error(f"Failed to load people: {msg}")
        )
        self._active_people_worker = worker
        start_worker(worker, self._thread_pool)

    def _on_people_list_ready(
        self,
        stacks: list[PersonStackSummary],
        cover_lookups: dict[int, tuple[str, int, int, int, int]],
        cover_highlight_pngs: dict[int, bytes],
        epoch: int,
    ) -> None:
        """Called on the main thread when people stacks are loaded."""
        # Ignore stale results from previous epochs
        if epoch != self._people_epoch:
            return

        self._active_people_worker = None
        self._people_browser.load_stacks(stacks, cover_lookups, cover_highlight_pngs)
        self._refresh_available_people_filter()

        threshold = (
            self._settings_service.get_face_review_threshold()
            if self._settings_service
            else 0
        )
        if stacks:
            self.statusBar().showMessage(
                f"Loaded {len(stacks)} person clusters (threshold: {threshold}+ images)"
            )
        else:
            self.statusBar().showMessage(f"No person clusters with {threshold}+ images")

    def _on_face_review_settings(self) -> None:
        """Open a dialog to configure face review threshold."""
        try:
            current = self._settings_service.get_face_review_threshold()
            value, ok = QInputDialog.getInt(
                self,
                "Face Review Threshold",
                "Minimum number of images per person cluster to show:",
                value=current,
                minValue=1,
                maxValue=100,
                step=1,
            )

            if ok:
                self._settings_service.set_face_review_threshold(value)
                self.statusBar().showMessage(f"Face review threshold set to {value}")

                # Refresh people list with new threshold
                if self._left_stack.currentIndex() == 1:  # If viewing people
                    self._refresh_people_list()
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to save threshold setting: {exc}")

    def _run_image_index(self) -> None:
        root = Path(str(getattr(self._runtime_settings, "photo_root_dir", ".")))

        # Validate path before indexing
        if not root.exists():
            self.statusBar().showMessage(
                "No photo library folder set. Please choose a folder."
            )
            self._on_select_library()
            return

        if not root.is_dir():
            self._show_error(f"Not a directory: {root}")
            return

        # Set initial task status
        self._set_task_status("⟳ Indexing images…")

        def on_progress(current: int, total: int) -> None:
            """Update status bar with indexing progress."""
            with contextlib.suppress(Exception):
                self._set_task_status(f"⟳ Indexing images {current}/{total}")

        # Create a safe wrapper for the indexing function
        def run_index() -> object:
            """Wrapper to call index_folder safely."""
            try:
                return self._image_index_service.index_folder(
                    root, on_progress=on_progress
                )
            except Exception:
                LOGGER.exception("Indexing error during library scan")
                raise

        worker = IndexWorker(
            run_index,
            progress_callback=lambda _current, _total: (
                None
            ),  # Progress comes from on_progress callback
        )

        def on_index_complete(result: object) -> None:
            """Handle indexing completion."""
            try:
                # result is an ImageIndexResult dataclass
                scanned = getattr(result, "scanned", 0)
                inserted = getattr(result, "inserted", 0)
                skipped = getattr(result, "skipped", 0)
                self._clear_task_status()
                self.statusBar().showMessage(
                    f"Index complete: {inserted} new images "
                    f"({scanned} scanned, {skipped} skipped)"
                )
                self._reload_album_tree()
                if inserted > 0:
                    # Trigger tile build + gallery reload so new images appear immediately
                    self._album_gallery_vm.select_library()
            except Exception as exc:
                LOGGER.exception("Error processing index result")
                self._show_error(f"Error processing index result: {exc}")

        worker.signals.result_ready.connect(on_index_complete)

        def on_index_error(err: str) -> None:
            self._clear_task_status()
            self._show_error(f"Indexing failed: {err}")

        worker.signals.error.connect(on_index_error)
        start_worker(worker, self._thread_pool)

    def _run_backfill_timestamps(self) -> None:
        """Fix images that have no capture_date, reading EXIF then falling back to mtime."""
        self._set_task_status("⟳ Fixing timestamps…")

        def run() -> object:
            def _cb(current: int, total: int) -> None:
                worker.signals.progress_detailed.emit(current, total)

            return self._image_index_service.backfill_missing_timestamps(
                on_progress=_cb
            )

        worker = IndexWorker(
            run,
            progress_callback=lambda current, total: (
                self._set_task_status(f"⟳ Fixing timestamps {current}/{total}…")
                if total > 0
                else None
            ),
        )

        def on_done(result: object) -> None:
            count = int(result) if isinstance(result, int) else 0
            self._clear_task_status()
            self.statusBar().showMessage(
                f"Timestamps fixed: {count} image(s) updated from file date"
            )
            if count > 0:
                self._album_gallery_vm.select_library()

        def on_err(msg: str) -> None:
            self._clear_task_status()
            self._show_error(f"Timestamp backfill failed: {msg}")

        worker.signals.result_ready.connect(on_done)
        worker.signals.error.connect(on_err)
        start_worker(worker, self._thread_pool)

    def _run_prune_missing_files(self) -> None:
        """Remove DB records for files that no longer exist on disk."""
        self._set_task_status("⟳ Checking for missing files…")

        def run() -> object:
            def _cb(current: int, total: int) -> None:
                worker.signals.progress_detailed.emit(current, total)

            return self._image_index_service.prune_missing_files(on_progress=_cb)

        worker = IndexWorker(
            run,
            progress_callback=lambda current, total: (
                self._set_task_status(f"⟳ Checking files {current}/{total}…")
                if total > 0
                else None
            ),
        )

        def on_done(result: object) -> None:
            count = int(result) if isinstance(result, int) else 0
            self._clear_task_status()
            self.statusBar().showMessage(
                f"Removed {count} stale record(s) for missing files"
            )
            if count > 0:
                self._reload_album_tree()
                self._album_gallery_vm.select_library()
                self._refresh_people_list()

        def on_err(msg: str) -> None:
            self._clear_task_status()
            self._show_error(f"Prune failed: {msg}")

        worker.signals.result_ready.connect(on_done)
        worker.signals.error.connect(on_err)
        start_worker(worker, self._thread_pool)

    def _open_duplicate_dialog(self) -> None:
        """Open the Find Duplicates dialog."""
        from photo_app.app.widgets.duplicate_review_dialog import (  # noqa: PLC0415
            DuplicateReviewDialog,
        )
        from photo_app.services.duplicate_detection_service import (  # noqa: PLC0415
            DuplicateDetectionService,
        )

        image_repo = self._album_vm.get_image_repository()
        if image_repo is None:
            self._show_error("Image repository not available.")
            return

        service = DuplicateDetectionService(
            image_repo,
            collection_names=self._collection_service.collection_names_by_image,
        )
        photo_root = Path(
            str(getattr(self._runtime_settings, "photo_root_dir", Path.home()))
        )
        trash_dir = photo_root / ".trash"

        dialog = DuplicateReviewDialog(
            service=service,
            default_trash_dir=trash_dir,
            thread_pool=self._thread_pool,
            parent=self,
        )
        dialog.exec()
        # Refresh gallery — duplicates that were trashed are now missing from the index
        self._refresh_after_library_change()
        # Trashing duplicates cascades to their faces, which can change or
        # remove person clusters — keep the People tab in sync too.
        self._refresh_people_list()

    def _run_face_index(self) -> None:  # noqa: C901
        if self._face_index_service is None:
            self._show_error("Face indexing unavailable")
            return

        face_batch_size = int(getattr(self._runtime_settings, "face_batch_size", 128))

        # Track totals across all batches for the completion message
        total_processed = 0
        total_detected = 0

        # Set initial task status
        self._set_task_status("⟳ Indexing faces…")

        def on_progress(current: int, total: int) -> None:
            try:
                overall = total_processed + current
                self._set_task_status(
                    f"⟳ Indexing faces — {current}/{total} (batch), {overall} total"
                )
            except Exception:
                LOGGER.exception("Failed to update face index progress")

        def on_cluster_progress(current: int, total: int) -> None:
            try:
                msg = f"⟳ Clustering faces ({current}/{total})…"
                QMetaObject.invokeMethod(
                    self._task_label,
                    b"setText",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, msg),
                )
            except Exception:
                LOGGER.exception("Failed to update face clustering progress")

        def run_face_index_loop() -> FaceIndexResult:
            """Detect faces in batches, then cluster once at the end."""
            nonlocal total_processed, total_detected

            # Phase 1: detection only — fast, no clustering overhead per batch
            while True:
                result = self._face_index_service.index_faces(
                    face_batch_size,
                    on_progress=on_progress,
                    skip_clustering=True,  # ← skip per-batch clustering
                )
                total_processed += result.processed_images
                total_detected += result.detected_faces
                if result.processed_images < face_batch_size:
                    break

            # Phase 2: cluster all newly detected faces once
            with contextlib.suppress(Exception):
                self._set_task_status("⟳ Clustering faces…")

            identity_cluster_service = (
                self._face_index_service.get_identity_cluster_service()
            )
            if identity_cluster_service is not None:
                identity_cluster_service.index_new_faces(
                    on_progress=on_cluster_progress
                )
            elif hasattr(self._face_index_service, "_cluster_all_faces"):
                # Skip clustering on first full index - too expensive with thousands of clusters
                # It will run on the next incremental index (small number of new images)
                pass

            # Skip run_all() on first full index — too expensive with thousands of clusters.
            # It will run on the next incremental index (small number of new images).
            # Only run the cache invalidation so the people tab refreshes.
            query_cache_service = self._face_index_service.get_query_cache_service()
            if query_cache_service is not None:
                query_cache_service.invalidate_all()

            return FaceIndexResult(
                processed_images=total_processed,
                detected_faces=total_detected,
            )

        worker = IndexWorker(run_face_index_loop)

        def on_face_index_result(result: object) -> None:
            self._clear_task_status()
            self.statusBar().showMessage(
                f"Face index complete: "
                f"{getattr(result, 'detected_faces', 0)} faces detected "
                f"across {getattr(result, 'processed_images', 0)} images"
            )

        worker.signals.result_ready.connect(on_face_index_result)
        worker.signals.result_ready.connect(self._refresh_people_list)

        def on_face_index_error(err: str) -> None:
            self._clear_task_status()
            self._show_error(err)

        worker.signals.error.connect(on_face_index_error)
        start_worker(worker, self._thread_pool)

    def _restore_geometry(self) -> None:
        size = self._ui_state.get("window_size")
        if isinstance(size, list) and len(size) == WINDOW_SIZE_VALUES:
            self.resize(QSize(int(size[0]), int(size[1])))
        else:
            self.resize(1600, 900)
        widths = self._ui_state.get("splitter_widths")
        if isinstance(widths, list) and len(widths) == SPLITTER_SIZE_VALUES:
            self._splitter.setSizes([int(widths[0]), int(widths[1]), int(widths[2])])

    def _save_ui_state(self) -> None:
        self._ui_state["window_size"] = [self.size().width(), self.size().height()]
        self._ui_state["splitter_widths"] = self._splitter.sizes()
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        if current is not None and current.album_id is not None:
            self._ui_state["last_opened_album"] = current.album_id
        # Preserve the album_tree written by AlbumViewModel so we don't overwrite it
        saved_tree = self._album_vm.get_serialized_tree()
        if saved_tree is not None:
            self._ui_state["album_tree"] = saved_tree
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(
            json.dumps(self._ui_state, indent=2),
            encoding="utf-8",
        )

    def _load_ui_state(self) -> dict[str, object]:
        if not self._settings_path.exists():
            return {}
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except OSError, ValueError:
            return {}

    def _persist_tree(self) -> None:
        self._album_vm.persist_tree(self._album_tree_model.all_nodes())
        self._save_ui_state()

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)
        self.statusBar().showMessage(message)

    def _on_loading_started(self) -> None:
        """Handle loading start - show spinner or loading indicator."""
        # For now, just update status bar
        # In a full implementation, we could show a spinner in the photo grid area

    def _on_loading_finished(self) -> None:
        """Handle loading completion."""
        # Reset any loading indicators

    def _on_gallery_status(self, message: str) -> None:
        """Handle gallery status updates without using main status bar."""
        # Gallery status updates are handled internally by the GalleryViewModel
        # and don't need to be shown in the main status bar
        # This method exists to prevent the status bar from being updated
        # by gallery operations

    def _on_select_library(self) -> None:
        """Open dialog to select the photo library folder."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Photo Library Folder",
            str(getattr(self._runtime_settings, "photo_root_dir", ".")),
        )
        if not folder:
            return

        folder_path = Path(folder)
        if not folder_path.exists():
            self._show_error(f"Folder does not exist: {folder}")
            return

        if not folder_path.is_dir():
            self._show_error(f"Not a directory: {folder}")
            return

        # Check if folder is readable
        try:
            list(folder_path.iterdir())
        except PermissionError:
            self._show_error(f"Permission denied accessing: {folder}")
            return
        except Exception as exc:
            LOGGER.exception("Cannot access folder: %s", folder)
            self._show_error(f"Cannot access folder: {exc}")
            return

        self.statusBar().showMessage(f"Library folder set to: {folder}")

        # Persist the new path to settings
        updated_settings = dataclasses.replace(
            self._runtime_settings, photo_root_dir=folder_path
        )
        self._settings_service.save_runtime_settings(updated_settings)
        self._runtime_settings = updated_settings

        # Optionally prompt to index immediately
        reply = QMessageBox.question(
            self,
            "Index Now?",
            f"Index images in {folder_path.name}?\n\n"
            f"This will scan subfolders recursively.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._run_image_index()

    def _on_file_create_album(self) -> None:
        """Create a new album from File menu."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        parent_id = current.node_id if current else None
        self._on_create_virtual_album(parent_id)

    def _on_export_html_gallery(self) -> None:
        """Export current album as HTML gallery."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        if current is None or current.album_id is None:
            self._show_error("Select an album to export.")
            return

        output_dir = QFileDialog.getExistingDirectory(
            self,
            "Export Gallery To...",
            str(Path.cwd()),
        )
        if not output_dir:
            return

        try:
            exporter_module = importlib.import_module(
                "photo_app.services.html_gallery_exporter"
            )
            html_gallery_exporter_cls = exporter_module.HtmlGalleryExporter
            # For now, use a simple image repository mock
            # In production, you'd inject this properly
            exporter = html_gallery_exporter_cls(self._album_service)
            result = exporter.generate_gallery(
                album_id=current.album_id,
                output_dir=Path(output_dir),
                title=f"{current.name} - Photo Gallery",
                group_by="date",
            )

            if result["html_file"]:
                QMessageBox.information(
                    self,
                    "Gallery Exported",
                    f"Gallery exported to:\n{result['html_file']}\n\n"
                    f"Total images: {result['total_images']}",
                )
                self.statusBar().showMessage(f"Gallery exported: {result['html_file']}")
            else:
                self._show_error("No images in album to export.")
        except Exception as exc:
            LOGGER.exception("Export failed")
            self._show_error(f"Export failed: {exc}")

    def _open_import_dialog(self) -> None:
        """Open the import dialog."""
        dialog = ImportDialog(
            image_index_service=self._image_index_service,
            face_index_service=self._face_index_service,
            default_dest_path=self._settings_service.get_runtime_settings().photo_root_dir,
            parent=self,
        )
        dialog.set_thread_pool(self._thread_pool)
        dialog.exec()
        if dialog.files_copied() > 0:
            self._refresh_after_library_change()

    def _on_cast_to_tv(self) -> None:
        """Open Chromecast slideshow dialog for the current album."""
        from photo_app.services.chromecast_service import (  # noqa: PLC0415
            ChromecastService,
        )

        if self._chromecast_service is None:
            self._chromecast_service = ChromecastService()

        image_paths = [item.file_path for item in self._album_photo_model.items]
        if not image_paths:
            self.statusBar().showMessage("No images in the current album to cast.")
            return

        dialog = ChromecastSlideshowDialog(
            service=self._chromecast_service,
            image_paths=image_paths,
            parent=self,
        )
        dialog.exec()

    def _on_start_slideshow(self) -> None:
        """Start a fullscreen slideshow of whatever the current view shows."""
        if self._slideshow is not None:
            self._slideshow.activateWindow()
            return

        newest_first = self._newest_first_action.isChecked()
        if self._mode_stack.currentIndex() == 1:
            source = self._people_browser.slideshow_source()
            if source is None:
                self.statusBar().showMessage("Open a person to start a slideshow.")
                return
            entries, start_row = source
            items = self._person_slideshow_items(entries, newest_first=newest_first)
            start_id = entries[start_row][0] if 0 <= start_row < len(entries) else None
            self._slideshow_from_gallery = False
        else:
            if newest_first and self._album_gallery_vm.has_more:
                # The newest photos may not be paged in yet; start once they are.
                self._slideshow_waiting_for_all = True
                self.statusBar().showMessage("Loading all photos for the slideshow…")
                self._album_gallery_vm.load_all()
                return
            grid_items = self._album_photo_model.items
            if newest_first:
                grid_items = self._newest_first(grid_items)
            items = self._slideshow_items(grid_items)
            start_id = self._current_album_image_id()
            self._slideshow_from_gallery = True

        if newest_first:
            start_id = None  # always begin with the newest photo

        if not items:
            self.statusBar().showMessage("No photos in this view to show.")
            return

        start_index = next(
            (i for i, item in enumerate(items) if item.image_id == start_id), 0
        )
        filtered = self._filter_slideshow_items(items)
        if len(filtered) != len(items):
            # Begin at the first remaining photo at or after the start point.
            kept_ids = {item.image_id for item in filtered}
            start_index = sum(
                1 for item in items[:start_index] if item.image_id in kept_ids
            )
            items = filtered
            if not items:
                self.statusBar().showMessage(
                    "No photos in this view match the slideshow options."
                )
                return
        slideshow = SlideshowWindow(
            items,
            start_index,
            seconds=self._slideshow_seconds(),
            advance_on_flag=self._advance_on_flag_action.isChecked(),
            parent=self,
        )
        slideshow.flag_requested.connect(self._on_slideshow_flag)
        slideshow.more_requested.connect(self._on_slideshow_more_requested)
        slideshow.seconds_changed.connect(self._on_slideshow_seconds_changed)
        # The A key in the slideshow flips the same option as the menu.
        slideshow.advance_on_flag_changed.connect(
            self._advance_on_flag_action.setChecked
        )
        slideshow.finished.connect(self._on_slideshow_finished)
        self._slideshow = slideshow
        slideshow.start()

    @staticmethod
    def _slideshow_items(grid_items: list[PhotoGridItem]) -> list[SlideshowItem]:
        """Stills only — videos are skipped (Live Photos show their still)."""
        return [
            SlideshowItem(item.image_id, item.file_path, item.flag)
            for item in grid_items
            if not is_video_path(item.file_path)
        ]

    @staticmethod
    def _newest_first(grid_items: list[PhotoGridItem]) -> list[PhotoGridItem]:
        """Reverse the grid's oldest-first order, keeping undated photos last."""
        dated = [item for item in grid_items if item.capture_date is not None]
        undated = [item for item in grid_items if item.capture_date is None]
        return dated[::-1] + undated

    def _person_slideshow_items(
        self, entries: list[tuple[int, str]], *, newest_first: bool
    ) -> list[SlideshowItem]:
        images: list[Image] = []
        image_repo = self._album_vm.get_image_repository()
        if image_repo is not None:
            try:
                images = image_repo.list_by_ids([image_id for image_id, _ in entries])
            except Exception:  # noqa: BLE001
                images = []
        by_id = {image.id: image for image in images if image.id is not None}
        flags = {image_id: image.flag for image_id, image in by_id.items()}
        if newest_first:
            dates = {i: image.capture_date for i, image in by_id.items()}
            dated = [e for e in entries if dates.get(e[0]) is not None]
            undated = [e for e in entries if dates.get(e[0]) is None]
            dated.sort(key=lambda e: dates[e[0]], reverse=True)
            entries = dated + undated
        return [
            SlideshowItem(image_id, file_path, flags.get(image_id))
            for image_id, file_path in entries
            if not is_video_path(file_path)
        ]

    def _current_album_image_id(self) -> int | None:
        """The photo open in the detail view, else the grid's current photo."""
        if self._viewing_mode == "detail":
            items = self._image_detail_panel.get_items()
            index = self._image_detail_panel.get_current_index()
            return items[index].image_id if 0 <= index < len(items) else None
        current = self._album_photo_grid.currentIndex()
        if not current.isValid():
            return None
        image_id = self._album_photo_model.data(current, PhotoGridModel.ImageIdRole)
        return image_id if isinstance(image_id, int) else None

    def _on_page_for_slideshow(
        self,
        items: list[PhotoGridItem],
        append: bool,  # noqa: FBT001
    ) -> None:
        if not append:
            self._slideshow_waiting_for_all = False  # the view changed meanwhile
            return
        if self._slideshow_waiting_for_all:
            if not self._album_gallery_vm.has_more:
                self._slideshow_waiting_for_all = False
                self._on_start_slideshow()
            return
        if (
            self._slideshow is not None
            and self._slideshow_from_gallery
            and not self._newest_first_action.isChecked()
        ):
            new_items = self._filter_slideshow_items(self._slideshow_items(items))
            if not new_items:
                # Nothing on this page matches the options: keep looking.
                self._album_gallery_vm.load_next_page()
                return
            self._slideshow.append_items(new_items)

    def _on_slideshow_more_requested(self) -> None:
        # Newest-first already holds the whole view.
        if self._slideshow_from_gallery and not self._newest_first_action.isChecked():
            self._album_gallery_vm.load_next_page()

    def _on_slideshow_flag(self, image_id: int, flag_value: str | None) -> None:
        """Save a flag set in the slideshow and update the grid's badge."""
        try:
            image_repo = self._album_vm.get_image_repository()
            if image_repo is None:
                self._show_error("Image repository not available")
                return
            image_repo.update_flag(image_id, flag_value)
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to update flag: {exc}")
            return
        for row, item in enumerate(self._album_photo_model.items):
            if item.image_id == image_id:
                self._album_photo_model.update_flag(row, flag_value)
                break

    def _slideshow_seconds(self) -> int:
        seconds = self._ui_state.get("slideshow_seconds", DEFAULT_SECONDS)
        return seconds if isinstance(seconds, int) else DEFAULT_SECONDS

    def _update_seconds_action(self) -> None:
        self._seconds_action.setText(
            f"Seconds per Photo: {self._slideshow_seconds()}…\t↑/↓ while running"
        )

    def _on_choose_slideshow_seconds(self) -> None:
        seconds, ok = QInputDialog.getInt(
            self,
            "Slideshow Speed",
            "Seconds per photo:",
            self._slideshow_seconds(),
            MIN_SECONDS,
            MAX_SECONDS,
        )
        if ok:
            self._on_slideshow_seconds_changed(seconds)
            self._save_ui_state()

    def _on_slideshow_seconds_changed(self, seconds: int) -> None:
        self._ui_state["slideshow_seconds"] = seconds
        self._update_seconds_action()

    def _filter_slideshow_items(
        self, items: list[SlideshowItem]
    ) -> list[SlideshowItem]:
        """Apply the slideshow menu's flag and face options."""
        if self._skip_flagged_action.isChecked():
            items = [item for item in items if item.flag is None]
        mode = self._slideshow_face_mode()
        image_repo = self._album_vm.get_image_repository()
        if mode == "all" or not items or image_repo is None:
            return items
        try:
            keep = _ids_matching_face_mode(
                image_repo, [item.image_id for item in items], mode
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to check faces: {exc}")
            return items
        return [item for item in items if item.image_id in keep]

    def _slideshow_face_mode(self) -> str:
        return next(
            (m for m, a in self._face_mode_actions.items() if a.isChecked()), "all"
        )

    def _count_slideshow_photos(self) -> None:
        """Show on the menu how many photos the current settings would show.

        Counts the whole view (not just the pages loaded into the grid) on
        a worker thread, since that can mean loading every image record.
        """
        self._slideshow_count_request += 1
        request = self._slideshow_count_request
        image_repo = self._album_vm.get_image_repository()
        resolve_ids: Callable[[], list[int]] | None
        if self._mode_stack.currentIndex() == 1:
            source = self._people_browser.slideshow_source()
            person_ids = [image_id for image_id, _ in source[0]] if source else None
            resolve_ids = None if person_ids is None else lambda: person_ids
        else:
            resolve_ids = self._album_gallery_vm.view_image_ids_resolver()
        if resolve_ids is None or image_repo is None:
            self._set_start_slideshow_count(None)
            return

        self._start_slideshow_action.setText("Start Slideshow — counting…\tF5")
        skip_flagged = self._skip_flagged_action.isChecked()
        face_mode = self._slideshow_face_mode()
        worker = AlbumQueryWorker(
            lambda: _count_slideshow_photos(
                image_repo,
                resolve_ids(),
                skip_flagged=skip_flagged,
                face_mode=face_mode,
            )
        )

        def on_result(count: object) -> None:
            if request == self._slideshow_count_request:
                self._set_start_slideshow_count(
                    count if isinstance(count, int) else None
                )

        def on_error(message: str) -> None:
            LOGGER.warning("Slideshow count failed: %s", message)
            if request == self._slideshow_count_request:
                self._set_start_slideshow_count(None)

        worker.signals.result_ready.connect(on_result)
        worker.signals.error.connect(on_error)
        start_worker(worker, self._thread_pool)

    def _set_start_slideshow_count(self, count: int | None) -> None:
        if count is None:
            text = "Start Slideshow"
        elif count == 0:
            text = "Start Slideshow — no matching photos"
        else:
            text = f"Start Slideshow — {count:,} photo{'s' if count != 1 else ''}"
        self._start_slideshow_action.setText(f"{text}\tF5")

    def _on_slideshow_face_mode_changed(self, mode: str) -> None:
        self._ui_state["slideshow_faces"] = mode
        if self._slideshow is None:
            self._save_ui_state()

    def _on_slideshow_skip_flagged_changed(self, enabled: bool) -> None:  # noqa: FBT001
        self._ui_state["slideshow_skip_flagged"] = enabled
        if self._slideshow is None:
            self._save_ui_state()

    def _on_slideshow_newest_first_changed(self, enabled: bool) -> None:  # noqa: FBT001
        self._ui_state["slideshow_newest_first"] = enabled
        if self._slideshow is None:
            self._save_ui_state()

    def _on_slideshow_advance_on_flag_changed(self, enabled: bool) -> None:  # noqa: FBT001
        self._ui_state["slideshow_advance_on_flag"] = enabled
        if self._slideshow is None:
            self._save_ui_state()  # saved on close while a slideshow runs

    def _on_slideshow_finished(self, image_id: int) -> None:
        """Land on the photo the slideshow ended on."""
        self._slideshow = None
        self._save_ui_state()
        if image_id < 0 or not self._slideshow_from_gallery:
            return
        items = self._album_photo_model.items
        row = next(
            (i for i, item in enumerate(items) if item.image_id == image_id), None
        )
        if row is None:
            return
        if self._viewing_mode == "detail":
            self._image_detail_panel.load_image(items, row)
        else:
            index = self._album_photo_model.index(row, 0)
            self._album_photo_grid.setCurrentIndex(index)
            self._album_photo_grid.scrollTo(index)
        self._on_detail_image_selected(image_id)

    def _open_iphone_import_dialog(self) -> None:
        """Open the iPhone import dialog."""
        dialog = IPhoneImportDialog(
            image_index_service=self._image_index_service,
            face_index_service=self._face_index_service,
            default_dest_path=self._settings_service.get_runtime_settings().photo_root_dir,
            parent=self,
        )
        dialog.set_thread_pool(self._thread_pool)
        dialog.exec()
        if dialog.files_copied() > 0:
            self._refresh_after_library_change()

    # Keyboard shortcut handlers
    def _on_shortcut_rating_0(self) -> None:
        """Clear rating (shortcut: 0)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(0)
        self._on_rating_changed(0)

    def _on_shortcut_rating_1(self) -> None:
        """Set 1-star rating (shortcut: 1)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(1)
        self._on_rating_changed(1)

    def _on_shortcut_rating_2(self) -> None:
        """Set 2-star rating (shortcut: 2)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(2)
        self._on_rating_changed(2)

    def _on_shortcut_rating_3(self) -> None:
        """Set 3-star rating (shortcut: 3)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(3)
        self._on_rating_changed(3)

    def _on_shortcut_rating_4(self) -> None:
        """Set 4-star rating (shortcut: 4)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(4)
        self._on_rating_changed(4)

    def _on_shortcut_rating_5(self) -> None:
        """Set 5-star rating (shortcut: 5)."""
        if self._metadata_editor.get_current_image() is None:
            return
        self._metadata_editor.set_rating(5)
        self._on_rating_changed(5)

    def _on_shortcut_new_album(self) -> None:
        """Create new virtual album (shortcut: Ctrl+N)."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        parent_id = current.node_id if current else None
        self._on_create_virtual_album(parent_id)

    def _on_shortcut_new_folder(self) -> None:
        """Create new folder (shortcut: Ctrl+Shift+N)."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        parent_id = current.node_id if current else None
        self._on_create_folder(parent_id)

    def _on_shortcut_rename(self) -> None:
        """Rename selected album (shortcut: Ctrl+R)."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        if current:
            self._album_tree.edit(self._album_tree.currentIndex())

    def _on_shortcut_delete(self) -> None:
        """Delete selected album (shortcut: Delete)."""
        current = self._album_tree_model.node_from_index(
            self._album_tree.currentIndex()
        )
        if current:
            self._on_delete_node(current.node_id)

    def _on_shortcut_add_tag(self) -> None:
        """Add tag to current photo (shortcut: Ctrl+T)."""
        image = self._metadata_editor.get_current_image()
        if image is None:
            self._show_error("Select a photo first.")
            return
        self._metadata_editor.set_tags(self._metadata_editor.get_tags())
        self.statusBar().showMessage("Type tag and press Enter to add")

    def _on_shortcut_batch_faces(self) -> None:
        """Batch assign faces to person (shortcut: Ctrl+B)."""
        self.statusBar().showMessage("Batch face tagging not yet implemented")

    def _on_shortcut_next_photo(self) -> None:
        """Move to next photo (shortcut: Right Arrow)."""
        model = self._album_photo_model
        grid = self._album_photo_grid
        current = grid.currentIndex()
        if current.isValid():
            next_idx = model.index(current.row() + 1, 0)
            if next_idx.isValid():
                grid.setCurrentIndex(next_idx)
                grid.photoActivated.emit(
                    model.data(next_idx, PhotoGridModel.ImageIdRole)
                )

    def _on_shortcut_prev_photo(self) -> None:
        """Move to previous photo (shortcut: Left Arrow)."""
        model = self._album_photo_model
        grid = self._album_photo_grid
        current = grid.currentIndex()
        if current.isValid() and current.row() > 0:
            prev_idx = model.index(current.row() - 1, 0)
            if prev_idx.isValid():
                grid.setCurrentIndex(prev_idx)
                grid.photoActivated.emit(
                    model.data(prev_idx, PhotoGridModel.ImageIdRole)
                )

    def _flatten(self, nodes: list[AlbumTreeNode]) -> list[AlbumTreeNode]:
        """Flatten nested album tree nodes."""
        output: list[AlbumTreeNode] = []
        stack = list(nodes)
        while stack:
            node = stack.pop(0)
            output.append(node)
            stack.extend(node.children)
        return output

    def _find_index_by_album_id(self, album_id: int) -> QModelIndex:
        for node in self._flatten(self._album_tree_model.all_nodes()):
            if node.album_id == album_id:
                return self._album_tree_model.index_from_node_id(node.node_id)
        return QModelIndex()

    def _on_flag_changed(self, index: QModelIndex, flag_value: str | None) -> None:
        """Handle flag change from PhotoGridWidget."""
        try:
            # Get the image ID from the model
            image_id = self._album_photo_model.data(index, PhotoGridModel.ImageIdRole)
            if not isinstance(image_id, int):
                self._show_error("Invalid image ID")
                return

            # Update the flag in the database
            image_repo = self._album_vm.get_image_repository()
            if image_repo is None:
                self._show_error("Image repository not available")
                return
            image_repo.update_flag(image_id, flag_value)

            # Update the model item so the badge repaints immediately
            self._album_photo_model.update_flag(index.row(), flag_value)

            self.statusBar().showMessage(f"Flag updated: {flag_value or 'None'}")

        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to update flag: {exc}")

    def _on_delete_photos_requested(self, image_ids: list[int]) -> None:
        """Permanently delete selected photo(s) from disk and the library."""
        if not image_ids:
            return

        count = len(image_ids)
        noun = "photo" if count == 1 else "photos"
        raws, movs = self._image_index_service.count_companion_files(image_ids)
        extras = [
            f"{n} {label}{'' if n == 1 else 's'}"
            for n, label in ((raws, "RAW file"), (movs, "Live Photo video"))
            if n
        ]
        extra_text = f" (plus {' and '.join(extras)})" if extras else ""
        reply = QMessageBox.warning(
            self,
            "Delete Permanently",
            f"Permanently delete {count} {noun}{extra_text} from disk and the "
            "library?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        worker = IndexWorker(lambda: self._image_index_service.delete_images(image_ids))

        def on_done(result: object) -> None:
            deleted = int(getattr(result, "deleted", 0))
            failed = int(getattr(result, "failed", 0))
            if deleted:
                self._album_photo_model.remove_items(set(image_ids))
                # Belt-and-suspenders: force a full reload of the current
                # view too, so the grid is guaranteed to match the database
                # immediately rather than only on the next album switch.
                # Also updates the count badges in the album tree.
                self._refresh_after_library_change()
                # Deleting images cascades to their faces, which can change
                # or remove person clusters — refresh so the People tab
                # doesn't keep showing stale cards for deleted photos.
                self._refresh_people_list()
            if failed:
                self._show_error(
                    f"Deleted {deleted} {noun}, but {failed} could not be removed "
                    "(check file permissions)."
                )
            else:
                self.statusBar().showMessage(f"Permanently deleted {deleted} {noun}")

        def on_err(msg: str) -> None:
            self._show_error(f"Delete failed: {msg}")

        worker.signals.result_ready.connect(on_done)
        worker.signals.error.connect(on_err)
        start_worker(worker, self._thread_pool)

    def _on_filter_changed(self) -> None:
        """Handle filter changes from the filter bar."""
        # Get the current query definition from the filter bar
        query_definition = self._filter_bar.get_query_definition()

        # Update the GalleryViewModel with the new filter
        self._album_gallery_vm.update_filter_query(query_definition)

    def _on_save_filter_as_album(self, album_name: str) -> None:
        """Handle saving current filter as a virtual album."""
        try:
            # Get the current query definition from the filter bar
            query_definition = self._filter_bar.get_query_definition()

            # Get current album for parent context
            current = self._album_tree_model.node_from_index(
                self._album_tree.currentIndex()
            )
            parent_node_id = current.node_id if current else None

            # Create the virtual album
            node = self._album_vm.create_virtual_album(
                album_name.strip(),
                parent_node_id,
                query_definition,
            )

            # Add to tree and persist
            self._album_tree_model.add_node(node, parent_node_id)
            self._persist_tree()

            self.statusBar().showMessage(f"Saved filter as album: {album_name}")

        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Failed to save filter as album: {exc}")

    def _on_reset_identities_requested(self) -> None:
        """Wipe every person/name/cluster and re-cluster from scratch.

        Destructive: for a library whose clusters have become too mixed up
        to be worth untangling photo-by-photo.
        """
        if self._face_index_service is None:
            self._show_error("Face indexing service not available")
            return

        reply = QMessageBox.warning(
            self,
            "Reset All People?",
            "This deletes every person and name in your library — including "
            "any face you individually confirmed by hand — and re-clusters "
            "everyone from scratch.\n\n"
            "Photos, face detections, and embeddings are not touched; only "
            "who's-who assignments are reset. This cannot be undone. "
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_task_status("⟳ Resetting all people…")
        worker = IndexWorker(
            self._face_index_service.reset_all_identities,
            progress_callback=self._on_reset_identities_progress,
        )
        worker.signals.result_ready.connect(self._on_reset_identities_complete)
        worker.signals.error.connect(self._on_reset_identities_error)
        start_worker(worker, self._thread_pool)

    def _on_reset_identities_progress(self, current: int, total: int) -> None:
        """Update the status bar with reset progress."""
        self._set_task_status(f"⟳ Resetting all people… {current}/{total}")

    def _on_reset_identities_complete(self, result: object) -> None:
        """Called on the main thread when the reset finishes."""
        self._clear_task_status()
        assigned = result if isinstance(result, int) else "?"
        self.statusBar().showMessage(f"Reset complete: re-clustered {assigned} face(s)")
        self._refresh_people_list()

    def _on_reset_identities_error(self, msg: str) -> None:
        """Called on the main thread if the reset fails."""
        self._clear_task_status()
        self._show_error(f"Resetting people failed: {msg}")

    def _on_excluded_faces_requested(self) -> None:
        """Open the excluded-faces browser — restore anyone excluded by
        mistake (single "Remove" or bulk "Not of Interest"); nothing
        excluded is ever a one-way door.
        """
        dialog = ExcludedFacesDialog(
            self._face_review_service,
            self._cache_directory,
            self,
            tile_store=self._thumbnail_tile_store,
        )
        dialog.exec()
        self._refresh_people_list()

    def _on_reindex_faces_requested(self, file_path: str) -> None:
        """Re-index faces for a single image in a background worker."""
        if self._face_index_service is None:
            self._show_error("Face indexing service not available")
            return

        # Disable the button while running to prevent double-submission
        self._image_detail_panel.get_reindex_button().setEnabled(False)
        self._set_task_status(f"⟳ Re-indexing {Path(file_path).name}…")

        worker = IndexWorker(self._face_index_service.reindex_image, file_path)
        worker.signals.result_ready.connect(
            lambda result: self._on_reindex_complete(result, file_path)
        )
        worker.signals.error.connect(self._on_reindex_error)
        start_worker(worker, self._thread_pool)

    def _on_reindex_complete(self, result: object, file_path: str) -> None:
        """Called on the main thread when reindex_image finishes."""
        self._image_detail_panel.get_reindex_button().setEnabled(True)
        detected = getattr(result, "detected_faces", "?")
        self.statusBar().showMessage(
            f"Re-indexed {Path(file_path).name}: {detected} face(s) found"
        )
        # Reload the detail panel so new bboxes appear immediately
        items = self._image_detail_panel.get_items()
        idx = self._image_detail_panel.get_current_index()
        if items and idx < len(items):
            self._image_detail_panel.load_image(items, idx)

    def _on_reindex_error(self, msg: str) -> None:
        """Called on the main thread when reindex_image fails."""
        self._image_detail_panel.get_reindex_button().setEnabled(True)
        self._show_error(f"Re-index failed: {msg}")

    def _refresh_current_view(self) -> None:
        """Refresh the current view to show updated data."""
        # If in detail view, reload the current image
        if self._viewing_mode == "detail":
            current_items = self._image_detail_panel.get_items()
            current_index = self._image_detail_panel.get_current_index()
            if current_items and current_index < len(current_items):
                self._image_detail_panel.load_image(current_items, current_index)
        # If in gallery view, refresh the current album
        else:
            current_album_id = self._album_gallery_vm.get_current_album_id()
            if current_album_id:
                self._album_gallery_vm.select_album(current_album_id)

    def get_album_view(self) -> AlbumTreeWidget:
        """Get the album view widget for accessing its state."""
        return self._album_tree

    def get_gallery_view(self) -> PhotoGridWidget:
        """Get the gallery view widget for accessing its state."""
        return self._album_photo_grid

    def get_filter_bar(self) -> FilterBarWidget:
        """Get the filter bar widget for accessing its state."""
        return self._filter_bar

    def get_statusbar(self) -> QStatusBar:
        """Get the status bar widget for accessing its state."""
        return self.statusBar()
