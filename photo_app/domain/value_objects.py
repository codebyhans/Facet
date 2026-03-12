from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class BoundingBox:
    """Face location rectangle."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class AlbumQuery:
    """Typed virtual album query."""

    person_ids: tuple[int, ...] = ()
    cluster_ids: tuple[int, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    tag_names: tuple[str, ...] = ()
    rating_min: int | None = None
    quality_min: float | None = None
    camera_models: tuple[str, ...] = ()
    location_name: str | None = None
    gps_radius_km: float | None = None
    flags: tuple[str, ...] = ()
