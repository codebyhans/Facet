"""Integration example for using AdvancedFilterEditorDialog with album service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from photo_app.app.widgets import AdvancedFilterEditorDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from photo_app.domain.models import Album
    from photo_app.services.album_service import AlbumService
    from photo_app.services.tags_service import TagsService


class FilterDialogIntegration:
    """Utility for integrating AdvancedFilterEditorDialog with album services."""

    def __init__(
        self,
        album_service: AlbumService,
        tags_service: TagsService,
    ) -> None:
        """Initialize integration helper."""
        self.album_service = album_service
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
            name=album_name,
            query_definition=query_definition,
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
            album_id=album_id,
            query_definition=query_definition,
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
        all_persons = self.album_service.list_all_persons()
        all_tags = self.tags_service.get_all_tags()

        # Get available cameras from image repository
        # This would be a new method you might add to the service
        all_cameras = self._get_available_cameras()

        # Create and show dialog
        dialog = AdvancedFilterEditorDialog(
            parent=parent_widget,
            available_persons=all_persons,
            available_tags=sorted(all_tags),
            available_cameras=sorted(all_cameras),
            current_query=current_album.query_definition if current_album else None,
        )

        if dialog.exec() == dialog.Accepted:
            return dialog
        return None

    def _get_available_cameras(self) -> list[str]:
        """Get list of unique camera models from all images."""
        # This queries the repository for all distinct camera models
        # Implementation depends on adding a method to ImageRepository
        try:
            # Placeholder - implement this in ImageRepository.get_distinct_cameras()
            image_repo = getattr(self.album_service, "_image_repository", None)
        except (AttributeError, NotImplementedError):
            return []
        if image_repo and hasattr(image_repo, "get_distinct_cameras"):
            return image_repo.get_distinct_cameras()
        return []
