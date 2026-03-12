"""Widget for displaying person cards in a responsive grid layout."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPixmap, QMouseEvent, QContextMenuEvent, QEnterEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QMenu,
)

if TYPE_CHECKING:
    from photo_app.services.face_review_service import PersonStackSummary
    from photo_app.infrastructure.thumbnail_tiles import ThumbnailTileStore


class PersonCardWidget(QWidget):
    """Card widget for displaying a person cluster in the stacks view."""

    person_clicked = Signal(int, object)  # person_id, PersonStackSummary

    def __init__(
        self, 
        stack: PersonStackSummary, 
        tile_store: ThumbnailTileStore | None = None,
        parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.stack = stack
        self._tile_store = tile_store
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup UI for the person card."""
        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Cover image area
        cover_layout = QHBoxLayout()
        cover_layout.setContentsMargins(0, 0, 0, 0)
        
        self._cover_label = QLabel()
        self._cover_label.setFixedSize(120, 120)
        self._cover_label.setStyleSheet("border: 1px solid #444; background-color: #2a2a2a;")
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_layout.addWidget(self._cover_label, 0, Qt.AlignmentFlag.AlignCenter)
        main_layout.addLayout(cover_layout)

        # Info area
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        # Person name
        self._name_label = QLabel()
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        self._update_name_label()
        info_layout.addWidget(self._name_label)

        # Image count
        self._count_label = QLabel()
        self._count_label.setStyleSheet("font-size: 10px; color: #999;")
        self._count_label.setText(f"{self.stack.image_count} images")
        info_layout.addWidget(self._count_label)

        main_layout.addLayout(info_layout, 1)

        # Styling
        self.setStyleSheet(
            "background-color: #252526; border: 1px solid #3e3e42; border-radius: 4px;"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Set minimum size for grid layout
        self.setMinimumSize(140, 180)
        self.setMaximumWidth(200)

    def _update_name_label(self) -> None:
        """Update the name label with proper styling."""
        if self.stack.person_name:
            self._name_label.setText(self.stack.person_name)
            self._name_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #cccccc;")
        else:
            self._name_label.setText("Unnamed")
            self._name_label.setStyleSheet("font-weight: bold; font-size: 12px; color: #888888; font-style: italic;")

    def load_cover_image(self) -> None:
        """Load the cover image for this person using thumbnail tiles."""
        self._cover_label.clear()
        
        # Try to use thumbnail tile first for performance
        if self._tile_store and self.stack.cover_image_id is not None:
            try:
                tile_lookup = self._tile_store.get_image_tile(self.stack.cover_image_id)
                if tile_lookup and tile_lookup.tile_path.exists():
                    # Load the tile and crop the specific thumbnail
                    tile_pixmap = QPixmap(str(tile_lookup.tile_path))
                    if not tile_pixmap.isNull():
                        # Crop the specific thumbnail from the tile
                        cropped = tile_pixmap.copy(
                            tile_lookup.x, 
                            tile_lookup.y, 
                            tile_lookup.width, 
                            tile_lookup.height
                        )
                        self._cover_label.setPixmap(cropped)
                        return
            except Exception:
                pass
        
        # Fallback to loading full image if tile system fails
        if self.stack.cover_image_path:
            try:
                pixmap = QPixmap(str(Path(self.stack.cover_image_path)))
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        120, 120,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self._cover_label.setPixmap(scaled)
            except Exception:
                pass

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle mouse press to emit click signal."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.person_clicked.emit(self.stack.person_id, self.stack)
            # Visual feedback
            self.setStyleSheet(
                "background-color: #3a4a5a; border: 1px solid #4a9eff; border-radius: 4px;"
            )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Reset visual style on mouse release."""
        self.setStyleSheet(
            "background-color: #252526; border: 1px solid #3e3e42; border-radius: 4px;"
        )
        super().mouseReleaseEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        """Handle mouse enter for hover effect."""
        self.setStyleSheet(
            "background-color: #2d2d30; border: 1px solid #5a5a5a; border-radius: 4px;"
        )
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        """Reset style on mouse leave."""
        self.setStyleSheet(
            "background-color: #252526; border: 1px solid #3e3e42; border-radius: 4px;"
        )
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Handle right-click context menu."""
        from PySide6.QtWidgets import QMenu
        
        menu = QMenu(self)
        
        # Reclassify action
        reclassify_action = menu.addAction("Reclassify Person")
        reclassify_action.triggered.connect(self._on_reclassify)
        
        # Merge action
        merge_action = menu.addAction("Merge with...")
        merge_action.triggered.connect(self._on_merge)
        
        # Delete action
        delete_action = menu.addAction("Delete Person")
        delete_action.triggered.connect(self._on_delete)
        
        menu.exec(event.globalPos())

    def _on_reclassify(self) -> None:
        """Handle reclassify action."""
        # Emit signal to parent for reclassification
        pass

    def _on_merge(self) -> None:
        """Handle merge action."""
        # Emit signal to parent for merge
        pass

    def _on_delete(self) -> None:
        """Handle delete action."""
        # Emit signal to parent for delete
        pass



    def set_cover_pixmap(self, pixmap: QPixmap) -> None:
        """Set the cover image from an already-loaded and cropped QPixmap."""
        if not pixmap.isNull():
            self._cover_label.setPixmap(pixmap)
