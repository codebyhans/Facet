"""Keyboard shortcuts for the application."""

from __future__ import annotations

from typing import NamedTuple


class KeyboardShortcuts(NamedTuple):
    """Keyboard shortcut definitions."""

    # Rating shortcuts (0-5)
    RATING_0: str = "0"  # Clear rating
    RATING_1: str = "1"  # 1 star
    RATING_2: str = "2"  # 2 stars
    RATING_3: str = "3"  # 3 stars
    RATING_4: str = "4"  # 4 stars
    RATING_5: str = "5"  # 5 stars

    # Album operations
    NEW_ALBUM: str = "Ctrl+N"  # Create new album
    NEW_FOLDER: str = "Ctrl+Shift+N"  # Create new folder
    RENAME: str = "Ctrl+R"  # Rename selected album
    DELETE: str = "Delete"  # Delete selected album

    # Photo operations
    ADD_TAG: str = "Ctrl+T"  # Add tag to photo
    BATCH_FACES: str = "Ctrl+B"  # Batch face tagging
    NEW_VIRTUAL_ALBUM: str = "Ctrl+Alt+A"  # Create virtual album from filter
    FIND_ALBUM: str = "Ctrl+F"  # Find/filter albums

    # Navigation
    NEXT_PHOTO: str = "Right"  # Next photo
    PREV_PHOTO: str = "Left"  # Previous photo
    FIRST_PHOTO: str = "Home"  # First photo
    LAST_PHOTO: str = "End"  # Last photo

    # View operations
    FULLSCREEN: str = "F"  # Fullscreen
    ZOOM_IN: str = "Ctrl++"  # Zoom in
    ZOOM_OUT: str = "Ctrl+-"  # Zoom out
    FIT_TO_WINDOW: str = "Ctrl+0"  # Fit to window

    # Application
    QUIT: str = "Ctrl+Q"  # Quit app
    SETTINGS: str = "Ctrl+,"  # Settings/preferences
    INDEX_IMAGES: str = "Ctrl+I"  # Index images
    INDEX_FACES: str = "Ctrl+Shift+I"  # Index faces


def get_shortcuts() -> KeyboardShortcuts:
    """Get keyboard shortcuts configuration.

    Returns:
        KeyboardShortcuts instance with all shortcuts
    """
    return KeyboardShortcuts()


def describe_shortcuts() -> dict[str, str]:
    """Get human-readable shortcut descriptions.

    Returns:
        Dictionary mapping action names to shortcut keys and descriptions
    """
    shortcuts = get_shortcuts()
    return {
        "Rate 0 (Clear)": shortcuts.RATING_0,
        "Rate 1 Star": shortcuts.RATING_1,
        "Rate 2 Stars": shortcuts.RATING_2,
        "Rate 3 Stars": shortcuts.RATING_3,
        "Rate 4 Stars": shortcuts.RATING_4,
        "Rate 5 Stars": shortcuts.RATING_5,
        "New Album": shortcuts.NEW_ALBUM,
        "New Folder": shortcuts.NEW_FOLDER,
        "Rename Album": shortcuts.RENAME,
        "Delete Album": shortcuts.DELETE,
        "Add Tag": shortcuts.ADD_TAG,
        "Batch Face Tagging": shortcuts.BATCH_FACES,
        "Create Virtual Album": shortcuts.NEW_VIRTUAL_ALBUM,
        "Find Album": shortcuts.FIND_ALBUM,
        "Next Photo": shortcuts.NEXT_PHOTO,
        "Previous Photo": shortcuts.PREV_PHOTO,
        "First Photo": shortcuts.FIRST_PHOTO,
        "Last Photo": shortcuts.LAST_PHOTO,
        "Fullscreen": shortcuts.FULLSCREEN,
        "Zoom In": shortcuts.ZOOM_IN,
        "Zoom Out": shortcuts.ZOOM_OUT,
        "Fit to Window": shortcuts.FIT_TO_WINDOW,
        "Quit": shortcuts.QUIT,
        "Settings": shortcuts.SETTINGS,
        "Index Images": shortcuts.INDEX_IMAGES,
        "Index Faces": shortcuts.INDEX_FACES,
    }
