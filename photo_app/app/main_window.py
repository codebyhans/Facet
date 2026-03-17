from __future__ import annotations

import contextlib
import dataclasses
import importlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Q_ARG, QMetaObject, QModelIndex, QSize, Qt, QThreadPool
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from photo_app.app.models.album_tree_model import AlbumTreeModel, AlbumTreeNode
from photo_app.app.models.photo_grid_model import PhotoGridModel
from photo_app.app.view_models.album_view_model import AlbumViewModel
from photo_app.app.view_models.gallery_view_model import GalleryViewModel
from photo_app.app.widgets.advanced_filter_editor import AdvancedFilterEditorDialog
from photo_app.app.widgets.album_tree import AlbumTreeWidget
from photo_app.app.widgets.filter_bar import FilterBarWidget
from photo_app.app.widgets.image_detail_panel import ImageDetailPanel
from photo_app.app.widgets.metadata_editor import MetadataEditorPanel
from photo_app.app.widgets.people_browser import PeopleBrowser
from photo_app.app.widgets.photo_grid import PhotoGridWidget
from photo_app.app.widgets.photo_viewer import PhotoViewerWidget
from photo_app.app.workers.indexing_worker import IndexWorker
from photo_app.app.workers.people_list_worker import PeopleListWorker
from photo_app.config.constants import (
    DEFAULT_PAGE_SIZE,
    TEST_FACE_COUNT,
)
from photo_app.config.keyboard_shortcuts import get_shortcuts
from photo_app.services.face_index_service import FaceIndexResult
from photo_app.services.face_review_service import PersonStackSummary

if TYPE_CHECKING:
    from photo_app.app.models.photo_grid_model import PhotoGridItem
    from photo_app.domain.models import Image
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore
    from photo_app.services.album_service import AlbumService
    from photo_app.services.face_index_service import FaceIndexService
    from photo_app.services.face_review_service import FaceReviewItem, FaceReviewService
    from photo_app.services.image_index_service import ImageIndexService
    from photo_app.services.metadata_sync_service import MetadataSyncService
    from photo_app.services.settings_service import RuntimeSettings, SettingsService
    from photo_app.services.tags_service import TagService

LOGGER = logging.getLogger(__name__)
WINDOW_SIZE_VALUES = 2
SPLITTER_SIZE_VALUES = 3


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


class FaceAssignDialog(QDialog):
    """Assign one person name to one or many faces in a selected image."""

    def __init__(
        self, faces: list[FaceReviewItem], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Name Faces")
        self._faces = faces
        self._list = QListWidget(self)
        for face in faces:
            face_id = int(getattr(face, "face_id", 0))
            person_name = getattr(face, "person_name", None) or "Unknown"
            self._list.addItem(f"Face {face_id} ({person_name})")
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Enter person name")

        buttons = QDialogButtonBox(self)
        apply_one = buttons.addButton(
            "Apply to Selected", QDialogButtonBox.ButtonRole.AcceptRole
        )
        apply_all = buttons.addButton(
            "Apply to All", QDialogButtonBox.ButtonRole.ActionRole
        )
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addWidget(self._name)
        layout.addWidget(buttons)

        apply_one.clicked.connect(lambda: self.done(1))
        apply_all.clicked.connect(lambda: self.done(2))
        cancel.clicked.connect(self.reject)

    def selected_face_ids(self) -> list[int]:
        """Return selected face IDs according to dialog action."""
        if self.result() == TEST_FACE_COUNT:
            return [int(getattr(face, "face_id", 0)) for face in self._faces]
        row = self._list.currentRow()
        if row < 0 or row >= len(self._faces):
            return []
        return [int(getattr(self._faces[row], "face_id", 0))]

    def entered_name(self) -> str:
        """Return entered person name."""
        return self._name.text().strip()


class MergePersonDialog(QDialog):
    """Dialog for merging one person into another person."""

    def __init__(
        self,
        source_stack: PersonStackSummary,
        target_stacks: list[PersonStackSummary],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge Person")
        self._source_stack = source_stack
        self._target_stacks = target_stacks

        layout = QVBoxLayout(self)

        # Source person info
        source_label = QLabel(
            f"Merge '{source_stack.person_name or f'Cluster #{source_stack.person_id}'}' into:"
        )
        source_label.setStyleSheet("font-weight: bold; color: #cccccc;")
        layout.addWidget(source_label)

        # Target person selection
        self._target_combo = QListWidget(self)
        for stack in target_stacks:
            self._target_combo.addItem(
                f"{stack.person_name} ({stack.image_count} images)"
            )
        if self._target_combo.count() > 0:
            self._target_combo.setCurrentRow(0)

        layout.addWidget(self._target_combo)

        # Warning message
        warning_label = QLabel(
            "This will merge all faces from the source person into the target person."
        )
        warning_label.setStyleSheet("color: #ffcc00; font-size: 11px;")
        layout.addWidget(warning_label)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setMinimumWidth(300)

    def selected_person_id(self) -> int | None:
        """Return the selected target person ID."""
        current_row = self._target_combo.currentRow()
        if current_row >= 0 and current_row < len(self._target_stacks):
            return self._target_stacks[current_row].person_id
        return None

    def selected_person_name(self) -> str | None:
        """Return the selected target person name."""
        current_row = self._target_combo.currentRow()
        if current_row >= 0 and current_row < len(self._target_stacks):
            return self._target_stacks[current_row].person_name
        return None


class MainWindow(QMainWindow):
    """Lightroom-style 3-panel layout: Albums (left) | Photos (center) | Metadata (right)."""

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        image_index_service: ImageIndexService,
        album_service: AlbumService,
        face_index_service: FaceIndexService,
        face_review_service: FaceReviewService,
        metadata_sync_service: MetadataSyncService,
        tag_service: TagService,
        _settings_service: SettingsService,
        runtime_settings: RuntimeSettings,
        thumbnail_tile_store: ThumbnailTileStore,
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
        self._runtime_settings = runtime_settings
        self._thread_pool = QThreadPool.globalInstance()

        # Worker references to prevent GC
        self._active_face_index_worker: IndexWorker | None = None
        self._active_image_index_worker: IndexWorker | None = None
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

        nav_layout.addWidget(self._albums_btn)
        nav_layout.addWidget(self._people_btn)
        nav_layout.addStretch()

        # Create left panel for albums tree only (no people browser)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Left stack now only contains album tree
        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(self._album_tree)  # Index 0 - Albums only
        self._left_stack.setCurrentIndex(0)
        left_layout.addWidget(self._left_stack, 1)

        # Create filter bar
        self._filter_bar = FilterBarWidget()
        self._filter_bar.filter_changed.connect(self._on_filter_changed)

        # Create stacked widget for center panel (gallery or detail view)
        center_stack = QStackedWidget()
        center_stack.addWidget(self._album_photo_grid)  # Index 0
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

        self._current_viewer: PhotoViewerWidget | None = None
        self._wire_signals()
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
        self._album_gallery_vm.select_library()
        self._refresh_people_list()
        self._restore_geometry()
        self.setWindowTitle("Photo Browser - Lightroom Edition")

    def _set_task_status(self, message: str) -> None:
        """Show a persistent background-task message (left side of status bar)."""
        self._task_label.setText(message)

    def _clear_task_status(self) -> None:
        """Clear the background-task message when work finishes."""
        self._task_label.setText("")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._save_ui_state()
        super().closeEvent(event)

    def _wire_signals(self) -> None:
        self._album_vm.error.connect(self._show_error)
        self._album_gallery_vm.error.connect(self._show_error)
        self._album_gallery_vm.status.connect(self._on_gallery_status)
        self._album_gallery_vm.loading_started.connect(self._on_loading_started)
        self._album_gallery_vm.loading_finished.connect(self._on_loading_finished)

        self._album_tree.albumSelected.connect(
            lambda album_id, qd: self._album_gallery_vm.select_album(
                album_id, query_definition=qd
            )
        )
        self._album_tree.albumSelected.connect(self._on_album_selected)
        self._album_tree.albumMoved.connect(self._on_album_moved)
        self._album_tree.createFolderRequested.connect(self._on_create_folder)
        self._album_tree.createVirtualAlbumRequested.connect(
            self._on_create_virtual_album
        )
        self._album_tree.renameRequested.connect(self._on_rename_node)
        self._album_tree.deleteRequested.connect(self._on_delete_node)
        self._album_tree.editFiltersRequested.connect(self._on_edit_filters)
        self._album_tree.moveRequested.connect(self._on_move_node)

        self._album_gallery_vm.page_ready.connect(
            lambda items, append: self._on_gallery_page(
                self._album_photo_model, items, append
            )
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

        # Image detail panel signals
        self._image_detail_panel.closed.connect(self._on_detail_panel_closed)
        self._image_detail_panel.image_selected.connect(self._on_detail_image_selected)
        self._image_detail_panel.reindex_requested.connect(
            self._on_reindex_faces_requested
        )

        # Metadata editor signals -> sync service
        self._metadata_editor.rating_changed.connect(self._on_rating_changed)
        self._metadata_editor.tags_changed.connect(self._on_tags_changed)

        # People browser signals
        self._people_browser.person_selected.connect(self._on_person_selected)
        self._people_browser.back_to_stacks.connect(self._on_back_to_stacks)
        self._people_browser.person_renamed.connect(self._on_person_renamed)
        self._people_browser.person_merge_requested.connect(
            self._on_person_merge_requested
        )
        self._people_browser.show_unnamed_changed.connect(self._on_show_unnamed_changed)
        self._people_browser.face_delete_requested.connect(
            self._on_face_delete_requested
        )
        self._people_browser.face_reassign_requested.connect(
            self._on_face_reassign_requested
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

    def _on_create_virtual_album(self, parent_id: object) -> None:
        # Get available options for the advanced filter dialog
        all_persons = self._album_vm.list_filter_people()

        # Show advanced filter dialog
        dialog = AdvancedFilterEditorDialog(
            parent=self,
            available_persons=all_persons,
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
        )

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            query_definition = dialog.get_query_definition()
            node.query_definition = self._album_vm.update_album_query(
                node,
                query_definition,
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

    def _on_move_node(self, node_id: str, new_parent: object) -> None:
        new_parent_id = new_parent if isinstance(new_parent, str) else None
        index = self._album_tree_model.index_from_node_id(node_id)
        node = self._album_tree_model.node_from_index(index)
        if node is None:
            return
        if not self._album_tree_model.can_move(node_id, new_parent_id):
            self._show_error("Cannot move album into its own subtree.")
            return
        moved = self._album_tree_model.move_node(node_id, new_parent_id)
        if not moved:
            return
        self._album_vm.move_album(node_id, new_parent_id)
        self._persist_tree()
        if node.album_id is not None:
            self._album_tree.albumMoved.emit(node.album_id, new_parent_id)

    def _on_album_moved(self, _album_id: int, _new_parent: object) -> None:
        self.statusBar().showMessage("Album moved")

    def _on_album_selected(self, album_id: int) -> None:
        """Handle album selection - no longer updates album label."""
        # Album selection is handled by the GalleryViewModel
        # The current album label has been removed as requested

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
        """Handle person rename from people browser."""
        try:
            self._face_review_service.rename_person_stack(person_id, name)
            self._refresh_people_list()
            self.statusBar().showMessage(f"Renamed person to '{name}'")
        except Exception as exc:
            LOGGER.exception("Failed to rename person")
            self._show_error(f"Failed to rename person: {exc}")

    def _on_person_merge_requested(self, person_id: int) -> None:
        """Handle person merge request from people browser."""
        try:
            # Get current person info
            current_stack = None
            for stack in self._people_browser.get_current_stacks():
                if stack.person_id == person_id:
                    current_stack = stack
                    break

            if current_stack is None:
                self._show_error("Person not found for merge operation")
                return

            # Get list of other people to merge with
            other_people = [
                stack
                for stack in self._people_browser.get_current_stacks()
                if stack.person_id != person_id and stack.person_name
            ]

            if not other_people:
                self._show_error("No other named people available to merge with")
                return

            # Create merge dialog
            merge_dialog = MergePersonDialog(current_stack, other_people, self)
            if merge_dialog.exec() == QDialog.DialogCode.Accepted:
                target_person_id = merge_dialog.selected_person_id()
                if target_person_id and target_person_id != person_id:
                    # Look up cluster IDs — both stacks are already in _current_stacks
                    source_cluster_id = current_stack.cluster_id
                    target_stack = next(
                        (
                            s
                            for s in self._people_browser.get_current_stacks()
                            if s.person_id == target_person_id
                        ),
                        None,
                    )
                    if (
                        source_cluster_id is None
                        or target_stack is None
                        or target_stack.cluster_id is None
                    ):
                        self._show_error(
                            "Cannot merge: one or both persons have no cluster assignment"
                        )
                        return
                    # Perform the merge using the correct method and cluster IDs
                    self._face_review_service.merge_identity_clusters(
                        source_cluster_id, target_stack.cluster_id
                    )
                    self._refresh_people_list()
                    self.statusBar().showMessage(
                        f"Merged person into '{merge_dialog.selected_person_name()}'"
                    )
                else:
                    self.statusBar().showMessage("Merge cancelled")
            else:
                self.statusBar().showMessage("Merge cancelled")

        except Exception as exc:
            LOGGER.exception("Failed to merge person")
            self._show_error(f"Failed to merge person: {exc}")

    def _on_show_unnamed_changed(self, show_unnamed: bool) -> None:  # noqa: FBT001
        """Handle show unnamed clusters toggle from people browser."""
        self._show_unnamed = show_unnamed
        self._refresh_people_list()

    def _on_face_delete_requested(self, face_id: int) -> None:
        """Remove a face detection from a cluster image."""
        try:
            self._face_review_service.remove_face(face_id)
            self._people_browser.reload_current_inspector_image()
            self.statusBar().showMessage("Face removed")
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Could not remove face: {exc}")

    def _on_face_reassign_requested(self, face_id: int, new_person_name: str) -> None:
        """Reassign a face to a different named person."""
        try:
            self._face_review_service.assign_name(face_id, new_person_name)
            self._people_browser.reload_current_inspector_image()
            self._refresh_people_list()
            self.statusBar().showMessage(f"Face reassigned to '{new_person_name}'")
        except Exception as exc:  # noqa: BLE001
            self._show_error(f"Could not reassign face: {exc}")

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
            min_image_count=threshold,
            show_unnamed=show_unnamed,
            epoch=self._people_epoch,
        )
        worker.signals.result_ready.connect(self._on_people_list_ready)
        worker.signals.error.connect(
            lambda msg: self._show_error(f"Failed to load people: {msg}")
        )
        self._active_people_worker = worker
        self._thread_pool.start(worker)

    def _on_people_list_ready(
        self,
        stacks: list[PersonStackSummary],
        cover_lookups: dict[int, tuple[str, int, int, int, int]],
        epoch: int,
    ) -> None:
        """Called on the main thread when people stacks are loaded."""
        # Ignore stale results from previous epochs
        if epoch != self._people_epoch:
            return

        self._active_people_worker = None
        self._people_browser.load_stacks(stacks, cover_lookups)

        try:
            all_people = self._face_review_service.get_available_people()
            named_people = [p for p in all_people if p.name]
            self._filter_bar.set_available_people(named_people)
        except Exception:
            LOGGER.exception("Failed to update available people filter")

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

    def _open_photo_viewer(self, image_id: int) -> None:
        """Open photo viewer window for the selected image."""
        rows = self._album_photo_model.items
        pairs = [(item.image_id, item.file_path) for item in rows]
        start_row = 0
        for idx, item in enumerate(rows):
            if item.image_id == image_id:
                start_row = idx
                break
        self._current_viewer = PhotoViewerWidget(pairs, start_row, self)
        self._current_viewer.show()

    def _run_image_index(self) -> None:  # noqa: C901
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
            progress_callback=lambda _current, _total: None,  # Progress comes from on_progress callback
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

        def on_image_index_finished() -> None:
            self._active_image_index_worker = None

        worker.signals.finished.connect(on_image_index_finished)
        self._active_image_index_worker = worker  # keep alive until finished
        self._thread_pool.start(worker)

    def _run_face_index(self) -> None:  # noqa: C901, PLR0915
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

        def on_face_index_finished() -> None:
            self._active_face_index_worker = None

        worker.signals.finished.connect(on_face_index_finished)
        self._active_face_index_worker = worker
        self._thread_pool.start(worker)

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
        QThreadPool.globalInstance().start(worker)

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
