from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

from photo_app.domain.value_objects import AlbumQuery


def parse_iso_date(value: str | None) -> date | None:
    """Convert ISO date string to date if present."""
    return None if value is None else date.fromisoformat(value)


def now_utc() -> datetime:
    """Provide centralized timestamp creation."""
    return datetime.now(tz=UTC)


def parse_album_query(definition: dict[str, object]) -> AlbumQuery:
    """Parse and validate an album query payload."""
    raw_person_ids = definition.get("person_ids", [])
    if isinstance(raw_person_ids, list):
        person_values = cast("list[object]", raw_person_ids)
    else:
        person_values = []
    person_ids: tuple[int, ...] = tuple(
        value for value in person_values if isinstance(value, int)
    )
    raw_cluster_ids = definition.get("cluster_ids", [])
    if isinstance(raw_cluster_ids, list):
        cluster_values = cast("list[object]", raw_cluster_ids)
    else:
        cluster_values = []
    cluster_ids: tuple[int, ...] = tuple(
        value for value in cluster_values if isinstance(value, int)
    )
    raw_date_from = definition.get("date_from")
    raw_date_to = definition.get("date_to")
    if isinstance(raw_date_from, date):
        date_from = raw_date_from
    elif isinstance(raw_date_from, str):
        date_from = parse_iso_date(raw_date_from)
    else:
        date_from = None
    if isinstance(raw_date_to, date):
        date_to = raw_date_to
    elif isinstance(raw_date_to, str):
        date_to = parse_iso_date(raw_date_to)
    else:
        date_to = None

    # Parse tag names
    raw_tag_names = definition.get("tag_names", [])
    tag_names: tuple[str, ...] = ()
    if isinstance(raw_tag_names, list):
        tag_names = tuple(
            str(t).lower().strip()
            for t in raw_tag_names
            if isinstance(t, str) and t.strip()
        )

    # Parse rating min
    rating_min: int | None = None
    raw_rating_min = definition.get("rating_min")
    if isinstance(raw_rating_min, int):
        rating_min = max(0, min(5, raw_rating_min))

    # Parse quality min
    quality_min: float | None = None
    raw_quality_min = definition.get("quality_min")
    if isinstance(raw_quality_min, (int, float)):
        quality_min = max(0.0, min(1.0, float(raw_quality_min)))

    # Parse camera models
    raw_camera_models = definition.get("camera_models", [])
    camera_models: tuple[str, ...] = ()
    if isinstance(raw_camera_models, list):
        camera_models = tuple(
            str(m).strip() for m in raw_camera_models if isinstance(m, str) and m.strip()
        )

    # Parse location name
    location_name: str | None = None
    raw_location = definition.get("location_name")
    if isinstance(raw_location, str) and raw_location.strip():
        location_name = raw_location.strip()

    # Parse GPS radius
    gps_radius_km: float | None = None
    raw_radius = definition.get("gps_radius_km")
    if isinstance(raw_radius, (int, float)):
        gps_radius_km = max(0.0, float(raw_radius))

    # Parse flags
    raw_flags = definition.get("flags", [])
    flags: tuple[str, ...] = ()
    if isinstance(raw_flags, list):
        valid = {"keep", "discard", "undecided"}
        flags = tuple(str(f) for f in raw_flags if isinstance(f, str) and f in valid)

    return AlbumQuery(
        person_ids=person_ids,
        cluster_ids=cluster_ids,
        date_from=date_from,
        date_to=date_to,
        tag_names=tag_names,
        rating_min=rating_min,
        quality_min=quality_min,
        camera_models=camera_models,
        location_name=location_name,
        gps_radius_km=gps_radius_km,
        flags=flags,
    )
