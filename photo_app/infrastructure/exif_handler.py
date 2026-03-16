"""EXIF and metadata reading/writing for image files."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import piexif
from PIL import Image as PILImage

logger = logging.getLogger(__name__)


class ExifMetadataHandler:
    """Read and write EXIF/XMP metadata in image files."""

    # EXIF IFD tags
    EXIF_TAGS = {
        "UserComment": 0x010E,  # ImageDescription in IFD0, but UserComment is 37510
        "Model": 271,  # Camera model in IFD0
        "LensMake": 42304,  # Lens make in Exif IFD (0xA586)
        "LensModel": 42305,  # Lens model in Exif IFD (0xA587)
        "DateTime": 306,  # DateTime in IFD0
        "DateTimeOriginal": 36867,  # DateTimeOriginal in Exif IFD (0x9003)
        "Rating": 18246,  # Rating in IFD0 (0x4746) - Windows/XMP property
    }

    # GPS IFD tags
    GPS_TAGS = {
        "GPSLatitude": 2,
        "GPSLatitudeRef": 1,
        "GPSLongitude": 4,
        "GPSLongitudeRef": 3,
        "GPSAltitude": 6,
        "GPSAltitudeRef": 5,
    }

    @staticmethod
    def read_exif(filepath: str) -> dict[str, Any]:
        """
        Read EXIF data from an image file.

        Args:
            filepath: Path to image file

        Returns:
            Dictionary with extracted metadata:
            - datetime_original: datetime or None
            - camera_model: str or None
            - lens_model: str or None
            - gps_latitude: float or None
            - gps_longitude: float or None
            - user_comment: str or None
            - keywords: list[str] or None
            - rating: int or None (1-5)
        """
        result: dict[str, Any] = {
            "datetime_original": None,
            "camera_model": None,
            "lens_model": None,
            "gps_latitude": None,
            "gps_longitude": None,
            "user_comment": None,
            "keywords": None,
            "rating": None,
        }

        try:
            # Try piexif first
            exif_dict = piexif.load(filepath)

            # Read DateTimeOriginal from Exif IFD
            if "Exif" in exif_dict:
                exif_ifd = exif_dict["Exif"]
                if 36867 in exif_ifd:  # DateTimeOriginal
                    try:
                        dt_str = exif_ifd[36867].decode("utf-8")
                        result["datetime_original"] = datetime.strptime(
                            dt_str, "%Y:%m:%d %H:%M:%S"
                        )
                    except (ValueError, AttributeError) as e:
                        logger.debug(f"Failed to parse DateTimeOriginal: {e}")

                # UserComment (37510 in Exif)
                if 37510 in exif_ifd:
                    try:
                        result["user_comment"] = exif_ifd[37510].decode("utf-8")
                    except (UnicodeDecodeError, AttributeError):
                        pass

            # Read camera model from IFD0
            if "0th" in exif_dict:
                ifd0 = exif_dict["0th"]
                if 271 in ifd0:  # Model
                    try:
                        result["camera_model"] = ifd0[271].decode("utf-8")
                    except (UnicodeDecodeError, AttributeError):
                        pass

            # Read GPS info
            if "GPS" in exif_dict:
                gps_ifd = exif_dict["GPS"]
                try:
                    # GPS Latitude
                    if 2 in gps_ifd:
                        lat_ref = (
                            gps_ifd.get(1, b"N").decode("utf-8")
                            if 1 in gps_ifd
                            else "N"
                        )
                        lat_tuple = gps_ifd[2]
                        lat = (
                            lat_tuple[0][0] / lat_tuple[0][1]
                            + lat_tuple[1][0] / lat_tuple[1][1] / 60
                            + lat_tuple[2][0] / lat_tuple[2][1] / 3600
                        )
                        result["gps_latitude"] = lat if lat_ref == "N" else -lat

                    # GPS Longitude
                    if 4 in gps_ifd:
                        lon_ref = (
                            gps_ifd.get(3, b"E").decode("utf-8")
                            if 3 in gps_ifd
                            else "E"
                        )
                        lon_tuple = gps_ifd[4]
                        lon = (
                            lon_tuple[0][0] / lon_tuple[0][1]
                            + lon_tuple[1][0] / lon_tuple[1][1] / 60
                            + lon_tuple[2][0] / lon_tuple[2][1] / 3600
                        )
                        result["gps_longitude"] = lon if lon_ref == "E" else -lon
                except (KeyError, IndexError, ZeroDivisionError, TypeError) as e:
                    logger.debug(f"Failed to parse GPS data: {e}")

        except Exception as e:
            logger.warning(f"Failed to read EXIF from {filepath}: {e}")

        # Also try PIL for XMP-embedded keywords (future enhancement)
        try:
            pil_img = PILImage.open(filepath)
            # Keywords might be in info dict
            if "exif" in pil_img.info or "xmp" in pil_img.info:
                pass  # Would parse XMP here
        except Exception as e:
            logger.debug(f"Failed to read PIL metadata: {e}")

        return result

    @staticmethod
    def write_exif(filepath: str, metadata: dict[str, Any]) -> None:
        """
        Write EXIF data to an image file.

        Args:
            filepath: Path to image file
            metadata: Dictionary with keys:
                - rating: int 1-5
                - user_comment: str
                - keywords: list[str]
                - gps_latitude: float
                - gps_longitude: float
                - camera_model: str (read-only, not written)

        Note:
            For RAW files, creates sidecar .xmp file instead.
            For JPEG/TIFF, writes directly to EXIF.
        """
        path = Path(filepath)

        # RAW formats: write sidecar XMP
        if path.suffix.lower() in {".cr3", ".cr2", ".nef", ".arw", ".dng"}:
            ExifMetadataHandler._write_xmp_sidecar(filepath, metadata)
            return

        # JPEG/TIFF: write EXIF directly
        if path.suffix.lower() in {".jpg", ".jpeg", ".tiff", ".tif"}:
            ExifMetadataHandler._write_exif_direct(filepath, metadata)
            return

        logger.warning(f"Unsupported format for EXIF write: {path.suffix}")

    @staticmethod
    def _write_exif_direct(filepath: str, metadata: dict[str, Any]) -> None:
        """Write EXIF directly to JPEG/TIFF file."""
        try:
            exif_dict = piexif.load(filepath)
        except Exception:
            # Create new EXIF structure if none exists
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}

        # Rating (Windows XMP standard: 18246 or custom)
        if "rating" in metadata and metadata["rating"] is not None:
            rating = metadata["rating"]
            if 0 <= rating <= 5:
                exif_dict["0th"][18246] = str(rating).encode("utf-8")

        # User comment (37510 in Exif)
        if "user_comment" in metadata and metadata["user_comment"] is not None:
            comment = metadata["user_comment"]
            exif_dict["Exif"][37510] = comment.encode("utf-8")

        # Keywords: store in UserComment for now (XMP would be better)
        # TODO: Add proper XMP support for Keywords

        try:
            exif_bytes = piexif.dump(exif_dict)
            piexif.insert(exif_bytes, filepath)
            logger.info(f"Wrote EXIF metadata to {filepath}")
        except Exception as e:
            logger.error(f"Failed to write EXIF to {filepath}: {e}")

    @staticmethod
    def _write_xmp_sidecar(filepath: str, metadata: dict[str, Any]) -> None:
        """Write XMP sidecar file for RAW images."""
        path = Path(filepath)
        sidecar_path = path.with_suffix(path.suffix + ".xmp")

        xmp_content = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xmp_content += '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        xmp_content += '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        xmp_content += '    <rdf:Description rdf:about="">\n'

        if "rating" in metadata and metadata["rating"] is not None:
            rating = metadata["rating"]
            xmp_content += f"      <xmp:Rating>{rating}</xmp:Rating>\n"

        if "user_comment" in metadata and metadata["user_comment"] is not None:
            comment = metadata["user_comment"]
            xmp_content += f"      <dc:description>{comment}</dc:description>\n"

        if metadata.get("keywords"):
            keywords = metadata["keywords"]
            xmp_content += "      <dc:subject>\n"
            xmp_content += "        <rdf:Bag>\n"
            for kw in keywords:
                xmp_content += f"          <rdf:li>{kw}</rdf:li>\n"
            xmp_content += "        </rdf:Bag>\n"
            xmp_content += "      </dc:subject>\n"

        xmp_content += "    </rdf:Description>\n"
        xmp_content += "  </rdf:RDF>\n"
        xmp_content += "</x:xmpmeta>\n"

        try:
            sidecar_path.write_text(xmp_content, encoding="utf-8")
            logger.info(f"Wrote XMP sidecar to {sidecar_path}")
        except Exception as e:
            logger.error(f"Failed to write XMP sidecar {sidecar_path}: {e}")
