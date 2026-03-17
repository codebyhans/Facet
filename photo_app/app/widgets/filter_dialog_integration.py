"""Integration example for using AdvancedFilterEditorDialog with album service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from photo_app.app.widgets import AdvancedFilterEditorDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from photo_app.domain.models import Album
    from photo_app.services.album_service import AlbumService
    from photo_app.services.face_review_service import FaceReviewService
    from photo_app.services.tags_service import TagService


class FilterDialogIntegration:
    """Utility for integrating AdvancedFilterEditorDialog with album services."""

    def __init__(
        self,
        album_service: AlbumService,
        face_review_service: FaceReviewService,
        tags_service: TagService,
    ) -> None:
        """Initialize integration helper."""
        self.album_service = album_service
        self.face_review_service = face_review_service
        self.tags_service = tags_service

    def create_album_from_dialog(
        self,
        dialog: AdvancedFilterEditorDialog,
        album_name: str,
    ) -> None:
        """Create an album using the filter dialog results.

        Args:
            dialog: The AdvancedFilterEditorDialog with user selections
            album_name: Name for the new album
        """
        query_definition = dialog.get_query_definition()
        self.album_service.create_album(
            album_name,
            query_definition,
        )

    def update_album_from_dialog(
        self,
        dialog: AdvancedFilterEditorDialog,
        album_id: int,
    ) -> None:
        """Update an existing album with new filter criteria.

        Args:
            dialog: The AdvancedFilterEditorDialog with user selections
            album_id: ID of the album to update
        """
        query_definition = dialog.get_query_definition()
        self.album_service.update_album_query(
            album_id,
            query_definition,
        )

    def show_filter_dialog(
        self,
        parent_widget: QWidget | None = None,
        current_album: Album | None = None,
    ) -> AdvancedFilterEditorDialog | None:
        """Show the advanced filter editor dialog.

        Args:
            parent_widget: Parent widget for the dialog
            current_album: Optional current AlbumQuery to load for editing

        Returns:
            AdvancedFilterEditorDialog if user accepts, None if cancelled
        """
        # Get available options for dropdowns
        all_persons = self.face_review_service.get_available_people()

        # Create and show dialog
        dialog = AdvancedFilterEditorDialog(
            parent=parent_widget,
            available_persons=all_persons,
            current_query=current_album.query_definition if current_album else None,
        )

        if dialog.exec() == dialog.DialogCode.Accepted:
            return dialog
        return None
