"""Read capture datetime from QuickTime MOV/MP4 files (pure Python, no extra deps)."""

from __future__ import annotations

import re
import struct
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from pathlib import Path

_MAC_EPOCH = datetime(1904, 1, 1, tzinfo=UTC)
_READ_SIZE = 512 * 1024  # fast path: metadata atoms are usually near the front
_MAX_MOOV_SIZE = 100 * 1024 * 1024  # sanity cap if moov has to be located by seeking


def read_quicktime_date(path: Path) -> datetime | None:
    """Return the capture datetime embedded in a QuickTime/MP4 file, or None.

    Tries com.apple.quicktime.creationdate first (ISO 8601, has local timezone),
    then falls back to the mvhd creation-time field (seconds since 1904-01-01 UTC).
    The returned datetime is always timezone-aware.

    Most files have their 'moov' metadata atom near the start, so a 512 KB read
    covers them. Some exporters (WhatsApp, screen recorders, etc.) write 'moov'
    at the end of the file instead — in that case we locate it by walking
    top-level atom headers with seeks, without reading the whole file.
    """
    try:
        with path.open("rb") as f:
            head = f.read(_READ_SIZE)
            dt = _read_apple_creationdate(head)
            if dt is not None:
                return dt
            moov = _find_atom(head, b"moov")
            if moov is None:
                moov = _locate_moov_by_seeking(f)
            if moov is None:
                return None
            dt = _read_apple_creationdate(moov)
            if dt is not None:
                return dt
            return _read_mvhd_creation_time(moov)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# com.apple.quicktime.creationdate
# ---------------------------------------------------------------------------


def _read_apple_creationdate(data: bytes) -> datetime | None:
    key = b"com.apple.quicktime.creationdate"
    idx = data.find(key)
    if idx < 0:
        return None
    region = data[idx + len(key) : idx + len(key) + 256]
    m = re.search(
        rb"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:?\d{2})?)",
        region,
    )
    if not m:
        return None
    try:
        s = m.group(1).decode()
        s = s.replace("Z", "+00:00")
        # Normalize +HHMM -> +HH:MM (fromisoformat requires the colon on Python < 3.11)
        s = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
        return datetime.fromisoformat(s)
    except ValueError, UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# mvhd creation time
# ---------------------------------------------------------------------------


def _read_mvhd_creation_time(moov: bytes) -> datetime | None:
    mvhd = _find_atom(moov, b"mvhd")
    if mvhd is None or len(mvhd) < 5:  # noqa: PLR2004
        return None
    try:
        version = mvhd[0]
        if version == 0:
            creation_time = struct.unpack(">I", mvhd[4:8])[0]
        else:
            if len(mvhd) < 20:  # noqa: PLR2004
                return None
            creation_time = struct.unpack(">Q", mvhd[4:12])[0]
    except struct.error:
        return None
    if creation_time == 0:
        return None
    return _MAC_EPOCH + timedelta(seconds=creation_time)


# ---------------------------------------------------------------------------
# Atom walkers
# ---------------------------------------------------------------------------


def _find_atom(data: bytes, name: bytes) -> bytes | None:
    """Return the payload of the first child atom with the given 4-byte name."""
    pos = 0
    end = len(data)
    while pos + 8 <= end:
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        atom_name = data[pos + 4 : pos + 8]
        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", data[pos + 8 : pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
            header = 8
        else:
            header = 8
        if size < header:
            break
        if atom_name == name:
            return data[pos + header : pos + size]
        pos += size
    return None


def _locate_moov_by_seeking(f: BinaryIO) -> bytes | None:
    """Walk top-level atoms via header-only reads + seeks to find 'moov' anywhere
    in the file (e.g. appended at the end), without reading unrelated atoms."""
    f.seek(0, 2)
    file_size = f.tell()
    pos = 0
    while pos + 8 <= file_size:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:  # noqa: PLR2004
            break
        size = struct.unpack(">I", header[:4])[0]
        name = header[4:8]
        header_len = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:  # noqa: PLR2004
                break
            size = struct.unpack(">Q", ext)[0]
            header_len = 16
        elif size == 0:
            size = file_size - pos
        if size < header_len:
            break
        if name == b"moov":
            payload_size = size - header_len
            if payload_size > _MAX_MOOV_SIZE:
                return None
            f.seek(pos + header_len)
            data: bytes = f.read(payload_size)
            return data
        pos += size
    return None
