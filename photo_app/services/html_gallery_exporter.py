"""Generate self-contained HTML5 galleries with lightbox viewer."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from photo_app.domain.models import Image
    from photo_app.services.album_service import AlbumService

logger = logging.getLogger(__name__)


class HtmlGalleryExporter:
    """Generate self-contained HTML5 photo galleries."""

    def __init__(self, album_service: AlbumService) -> None:
        """Initialize HTML gallery exporter.

        Args:
            album_service: Service for loading album images
        """
        self._album_service = album_service

    def generate_gallery(
        self,
        album_id: int,
        output_dir: Path,
        title: str | None = None,
        group_by: str = "date",
    ) -> dict[str, object]:
        """Generate HTML5 gallery for album.

        Args:
            album_id: Album ID to export
            output_dir: Output directory for gallery files
            title: Optional gallery title
            group_by: Group images by 'date', 'person', or 'none'

        Returns:
            Dictionary with generation results:
                - html_file: Path to generated index.html
                - total_images: Number of images in gallery
                - gallery_url: URL to open in browser
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Get album images
        album_images = self._load_album_images(album_id)
        if not album_images:
            logger.warning("Album %s has no images", album_id)
            return {
                "html_file": None,
                "total_images": 0,
                "gallery_url": None,
            }

        # Organize images
        if group_by == "date":
            grouped = self._group_by_date(album_images)
        elif group_by == "person":
            grouped = self._group_by_person(album_images)
        else:
            grouped = {"All Photos": album_images}

        # Generate HTML
        gallery_title = title or "Photo Gallery"
        html_content = self._generate_html(gallery_title, grouped, album_images)

        # Write HTML file
        html_file = output_dir / "index.html"
        html_file.write_text(html_content, encoding="utf-8")

        # Copy images to gallery subdirectory
        images_dir = output_dir / "images"
        images_dir.mkdir(exist_ok=True)

        for image in album_images:
            try:
                src = Path(image.file_path)
                if src.exists():
                    dest = images_dir / src.name
                    shutil.copy2(src, dest)
            except Exception:
                logger.exception("Failed to copy image %s", src)

        result = {
            "html_file": str(html_file),
            "total_images": len(album_images),
            "gallery_url": html_file.as_uri(),
        }

        logger.info("Gallery generated: %s", html_file)
        return result

    def _group_by_date(self, images: list[Image]) -> dict[str, list[Image]]:
        """Group images by date."""
        grouped: dict[str, list[Image]] = {}
        for image in images:
            date_key = (
                image.capture_date.isoformat() if image.capture_date else "Unknown"
            )

            if date_key not in grouped:
                grouped[date_key] = []
            grouped[date_key].append(image)

        return dict(sorted(grouped.items(), reverse=True))

    def _group_by_person(self, images: list[Image]) -> dict[str, list[Image]]:
        """Group images by tagged person."""
        grouped: dict[str, list[Image]] = {"Untagged": []}
        for image in images:
            person_name = getattr(image, "person_name", None)
            if isinstance(person_name, str) and person_name:
                if person_name not in grouped:
                    grouped[person_name] = []
                grouped[person_name].append(image)
            else:
                grouped["Untagged"].append(image)

        return dict(sorted(grouped.items()))

    def _generate_html(
        self, title: str, grouped: dict[str, list[Image]], all_images: list[Image]
    ) -> str:
        """Generate HTML5 gallery content.

        Args:
            title: Gallery title
            grouped: Images organized by group
            all_images: All images for metadata

        Returns:
            HTML content as string
        """
        # Build image data for JavaScript
        image_data: list[dict[str, object]] = []
        for image in all_images:
            capture_date = image.capture_date
            date_value = (
                capture_date.isoformat() if isinstance(capture_date, date) else ""
            )
            image_data.append(
                {
                    "src": f"images/{Path(image.file_path).name}",
                    "alt": Path(image.file_path).stem,
                    "rating": image.rating or 0,
                    "date": date_value,
                }
            )

        image_json = json.dumps(image_data)

        # Build group HTML
        groups_html = ""
        for group_name, images in grouped.items():
            groups_html += '<section class="group">\n'
            groups_html += f"<h2>{group_name}</h2>\n"
            groups_html += '<div class="gallery">\n'

            for image in images:
                img_name = Path(image.file_path).name
                rating_stars = "★" * (image.rating or 0)
                groups_html += f"""
                <div class="gallery-item">
                    <img src="images/{img_name}" alt="{Path(image.file_path).stem}"
                         onclick="openLightbox(this.src)">
                    <div class="photo-info">
                        <p class="rating">{rating_stars}</p>
                    </div>
                </div>
                """

            groups_html += "</div>\n</section>\n"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            background-color: #1e1e1e;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            padding: 20px;
        }}

        h1 {{
            text-align: center;
            margin-bottom: 40px;
            font-size: 2.5rem;
        }}

        .group {{
            margin-bottom: 60px;
        }}

        .group h2 {{
            font-size: 1.5rem;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #0078d4;
        }}

        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            padding: 0 10px;
        }}

        .gallery-item {{
            position: relative;
            overflow: hidden;
            border-radius: 8px;
            background-color: #252525;
            cursor: pointer;
            transition: transform 0.3s, box-shadow 0.3s;
            border: 1px solid #3f3f3f;
        }}

        .gallery-item:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 16px rgba(0, 120, 212, 0.3);
        }}

        .gallery-item img {{
            width: 100%;
            height: 250px;
            object-fit: cover;
            display: block;
        }}

        .photo-info {{
            padding: 10px;
            background-color: rgba(0, 0, 0, 0.5);
        }}

        .rating {{
            color: #f7b801;
            font-size: 0.9rem;
        }}

        /* Lightbox */
        .lightbox {{
            display: none;
            position: fixed;
            z-index: 1000;
            padding: 0;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.95);
        }}

        .lightbox.active {{
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .lightbox-content {{
            position: relative;
            max-width: 90vw;
            max-height: 90vh;
        }}

        .lightbox-img {{
            max-width: 100%;
            max-height: 100%;
            border-radius: 4px;
        }}

        .lightbox-close {{
            position: absolute;
            top: 20px;
            right: 40px;
            font-size: 40px;
            font-weight: bold;
            color: #e0e0e0;
            cursor: pointer;
            background: none;
            border: none;
        }}

        .lightbox-close:hover {{
            color: #0078d4;
        }}

        .lightbox-nav {{
            position: absolute;
            top: 50%;
            font-size: 35px;
            font-weight: bold;
            color: #e0e0e0;
            background: none;
            border: none;
            cursor: pointer;
            padding: 10px 20px;
            transform: translateY(-50%);
            user-select: none;
        }}

        .lightbox-nav:hover {{
            color: #0078d4;
        }}

        .lightbox-prev {{
            left: 20px;
        }}

        .lightbox-next {{
            right: 20px;
        }}

        @media (max-width: 768px) {{
            .gallery {{
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                gap: 10px;
            }}

            h1 {{
                font-size: 1.5rem;
            }}

            .lightbox-nav {{
                font-size: 25px;
            }}
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {groups_html}

    <div id="lightbox" class="lightbox">
        <div class="lightbox-content">
            <button class="lightbox-close" onclick="closeLightbox()">&times;</button>
            <button class="lightbox-nav lightbox-prev" onclick="prevImage()">&lsaquo;</button>
            <img id="lightbox-img" class="lightbox-img" src="" alt="">
            <button class="lightbox-nav lightbox-next" onclick="nextImage()">&rsaquo;</button>
        </div>
    </div>

    <script>
        const images = {image_json};
        let currentImageIndex = 0;

        function openLightbox(src) {{
            currentImageIndex = images.findIndex(img => img.src === src);
            const lightbox = document.getElementById('lightbox');
            const lightboxImg = document.getElementById('lightbox-img');
            lightboxImg.src = src;
            lightbox.classList.add('active');
        }}

        function closeLightbox() {{
            const lightbox = document.getElementById('lightbox');
            lightbox.classList.remove('active');
        }}

        function prevImage() {{
            currentImageIndex = (currentImageIndex - 1 + images.length) % images.length;
            document.getElementById('lightbox-img').src = images[currentImageIndex].src;
        }}

        function nextImage() {{
            currentImageIndex = (currentImageIndex + 1) % images.length;
            document.getElementById('lightbox-img').src = images[currentImageIndex].src;
        }}

        // Keyboard navigation
        document.addEventListener('keydown', function(e) {{
            if (document.getElementById('lightbox').classList.contains('active')) {{
                if (e.key === 'ArrowLeft') prevImage();
                if (e.key === 'ArrowRight') nextImage();
                if (e.key === 'Escape') closeLightbox();
            }}
        }});
    </script>
</body>
</html>
"""

    def _load_album_images(self, album_id: int) -> list[Image]:
        """Load all images for an album by paging through results."""
        offset = 0
        limit = 500
        images: list[Image] = []
        while True:
            page = self._album_service.list_album_images(
                album_id,
                offset=offset,
                limit=limit,
            )
            if not page.items:
                break
            images.extend(page.items)
            if len(page.items) < limit:
                break
            offset += limit
        return images
