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
    person_ids = _extract_int_tuple(definition.get("person_ids"))
    cluster_ids = _extract_int_tuple(definition.get("cluster_ids"))
    date_from = _parse_date_field(definition.get("date_from"))
    date_to = _parse_date_field(definition.get("date_to"))
    tag_names = _extract_str_tuple(definition.get("tag_names"), normalize=True)
    rating_min = _parse_rating_min(definition.get("rating_min"))
    quality_min = _parse_quality_min(definition.get("quality_min"))
    camera_models = _extract_str_tuple(definition.get("camera_models"))
    location_name = _parse_location_name(definition.get("location_name"))
    gps_radius_km = _parse_gps_radius(definition.get("gps_radius_km"))
    flags = _parse_flags(definition.get("flags"))

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


def _extract_int_tuple(raw: object) -> tuple[int, ...]:
    if isinstance(raw, list):
        values = cast("list[object]", raw)
        return tuple(value for value in values if isinstance(value, int))
    return ()


def _extract_str_tuple(raw: object, *, normalize: bool = False) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    values = cast("list[object]", raw)
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        result.append(cleaned.lower() if normalize else cleaned)
    return tuple(result)


def _parse_date_field(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return parse_iso_date(raw)
    return None


def _parse_rating_min(raw: object) -> int | None:
    if isinstance(raw, int):
        return max(0, min(5, raw))
    return None


def _parse_quality_min(raw: object) -> float | None:
    if isinstance(raw, (int, float)):
        return max(0.0, min(1.0, float(raw)))
    return None


def _parse_location_name(raw: object) -> str | None:
    if isinstance(raw, str):
        cleaned = raw.strip()
        if cleaned:
            return cleaned
    return None


def _parse_gps_radius(raw: object) -> float | None:
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    return None


def _parse_flags(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    valid = {"keep", "discard", "undecided"}
    values = cast("list[object]", raw)
    return tuple(
        str(flag) for flag in values if isinstance(flag, str) and flag in valid
    )
