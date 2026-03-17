from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThumbnailItemViewModel:
    """UI item for thumbnail rendering."""

    image_id: int
    image_path: str
    thumbnail_path: str
    label: str
