from __future__ import annotations

import io
import os
import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from photo_app.services import import_dedup
from photo_app.services.import_dedup import FolderDuplicateChecker
from photo_app.services.import_service import ImportOptions, ImportService

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_MTIME = datetime(2024, 5, 17, 12, tzinfo=UTC).timestamp()


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (_MTIME, _MTIME))
    return path


def _dest_files(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.rglob("*") if p.is_file())


def _import(source: Path, dest: Path):  # noqa: ANN202
    return ImportService().run_import(
        ImportOptions(source_path=source, destination_path=dest)
    )


def test_reimport_skips_everything(tmp_path: Path) -> None:
    source, dest = tmp_path / "card", tmp_path / "library"
    originals = [_write(source / "a.jpg", b"aaaa"), _write(source / "b.jpg", b"bb")]

    first = _import(source, dest)
    second = _import(source, dest)

    assert first.copied == len(originals)
    assert second.copied == 0
    assert second.skipped_duplicates == len(originals)
    assert second.failed == 0
    assert _dest_files(dest) == ["a.jpg", "b.jpg"]
    assert all(r.status == "duplicate" for r in second.file_results)


def test_same_name_different_content_is_copied(tmp_path: Path) -> None:
    dest = tmp_path / "library"
    _write(tmp_path / "phone1" / "IMG_1234.jpg", b"first phone")
    _write(tmp_path / "phone2" / "IMG_1234.jpg", b"second phone")

    _import(tmp_path / "phone1", dest)
    summary = _import(tmp_path / "phone2", dest)

    assert summary.copied == 1
    assert summary.skipped_duplicates == 0
    assert _dest_files(dest) == ["IMG_1234.jpg", "IMG_1234_2.jpg"]


def test_same_content_different_name_is_skipped(tmp_path: Path) -> None:
    dest = tmp_path / "library"
    _write(tmp_path / "old" / "a.jpg", b"same bytes")
    _write(tmp_path / "new" / "renamed.jpg", b"same bytes")

    _import(tmp_path / "old", dest)
    summary = _import(tmp_path / "new", dest)

    assert summary.skipped_duplicates == 1
    assert _dest_files(dest) == ["a.jpg"]


def test_duplicates_within_one_batch(tmp_path: Path) -> None:
    source, dest = tmp_path / "card", tmp_path / "library"
    _write(source / "one" / "x.jpg", b"identical")
    _write(source / "two" / "y.jpg", b"identical")

    summary = _import(source, dest)

    assert summary.copied == 1
    assert summary.skipped_duplicates == 1
    assert len(_dest_files(dest)) == 1


def test_skip_duplicates_disabled_copies_all(tmp_path: Path) -> None:
    source, dest = tmp_path / "card", tmp_path / "library"
    _write(source / "a.jpg", b"aaaa")
    options = ImportOptions(
        source_path=source, destination_path=dest, skip_duplicates=False
    )

    ImportService().run_import(options)
    summary = ImportService().run_import(options)

    assert summary.copied == 1
    assert _dest_files(dest) == ["a.jpg", "a_2.jpg"]


def test_checker_only_hashes_same_size_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "dest"
    _write(folder / "existing.jpg", b"12345")
    source = _write(tmp_path / "src.jpg", b"1234")
    hashed: list[Path] = []
    real_hash = import_dedup.hash_file

    def tracking_hash(path: Path) -> str:
        hashed.append(path)
        return real_hash(path)

    monkeypatch.setattr(import_dedup, "hash_file", tracking_hash)

    assert FolderDuplicateChecker().find_duplicate(source, folder) is None
    assert hashed == []


def test_checker_missing_folder_and_register(tmp_path: Path) -> None:
    folder = tmp_path / "not_yet"
    source = _write(tmp_path / "src.jpg", b"content")
    checker = FolderDuplicateChecker()

    assert checker.find_duplicate(source, folder) is None

    copied = _write(folder / "copy.jpg", b"content")
    checker.register(copied)

    assert checker.find_duplicate(source, folder) == copied


def _mov_bytes(created: datetime) -> bytes:
    """Minimal QuickTime file: ftyp + moov/mvhd (version 0) with a creation time."""
    mac_epoch = datetime(1904, 1, 1, tzinfo=UTC)
    seconds = int((created - mac_epoch).total_seconds())
    mvhd_payload = b"\x00\x00\x00\x00" + struct.pack(">II", seconds, seconds)
    mvhd = struct.pack(">I", 8 + len(mvhd_payload)) + b"mvhd" + mvhd_payload
    moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
    ftyp = struct.pack(">I", 12) + b"ftypqt  "
    return ftyp + moov


def test_video_uses_quicktime_date_over_mtime(tmp_path: Path) -> None:
    source, dest = tmp_path / "extract", tmp_path / "library"
    _write(source / "clip.MOV", _mov_bytes(datetime(2021, 7, 15, 12, tzinfo=UTC)))

    summary = _import(source, dest)

    assert summary.copied == 1
    assert (dest / "2021" / "2021-07-15" / "clip.MOV").is_file()


def _png_bytes(xmp: str | None) -> bytes:
    info = PngInfo()
    if xmp is not None:
        info.add_itxt("XML:com.adobe.xmp", xmp)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


_SCREENSHOT_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description
 xmlns:exif="http://ns.adobe.com/exif/1.0/"
 xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">
 <exif:UserComment>Screenshot</exif:UserComment>
 <photoshop:DateCreated>2025-03-09T19:14:17</photoshop:DateCreated>
</rdf:Description></rdf:RDF></x:xmpmeta>"""


def test_screenshot_goes_to_screenshots_tree_by_xmp_date(tmp_path: Path) -> None:
    source, dest = tmp_path / "extract", tmp_path / "library"
    _write(source / "IMG_0001.PNG", _png_bytes(_SCREENSHOT_XMP))

    summary = _import(source, dest)

    assert summary.copied == 1
    assert (dest / "Screenshots" / "2025" / "2025-03-09" / "IMG_0001.PNG").is_file()


def test_undated_image_goes_to_undated_not_mtime_folder(tmp_path: Path) -> None:
    source, dest = tmp_path / "extract", tmp_path / "library"
    _write(source / "saved.png", _png_bytes(None))

    summary = _import(source, dest)

    assert not (dest / "Screenshots").exists()
    assert not (dest / "2024").exists()
    assert (dest / "undated" / "saved.png").is_file()
    assert summary.no_capture_date_count == 1
