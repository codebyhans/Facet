from __future__ import annotations

import http.server
import io
import logging
import mimetypes
import socket
import socketserver
import threading
import uuid
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_HEIC_SUFFIXES = frozenset({".heic", ".heif"})
_VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".avi", ".mts", ".m2ts", ".3gp"})


def _get_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return str(s.getsockname()[0])
        except OSError:
            return "127.0.0.1"


class _ImageHandler(http.server.BaseHTTPRequestHandler):
    """Serves registered images by UUID key."""

    def do_GET(self) -> None:
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "cast":  # noqa: PLR2004
            self.send_response(404)
            self.end_headers()
            return

        key = parts[1]
        server: _ImageServer = self.server  # type: ignore[assignment]
        file_path = server.registry.get(key)
        if file_path is None:
            self.send_response(404)
            self.end_headers()
            return

        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix in _HEIC_SUFFIXES:
            self._serve_heic_as_jpeg(path)
        else:
            mime = mimetypes.types_map.get(suffix, "image/jpeg")
            self._serve_file(path, mime)

    def _serve_file(self, path: Path, mime: str) -> None:
        try:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self.send_response(500)
            self.end_headers()

    def _serve_heic_as_jpeg(self, path: Path) -> None:
        try:
            from PIL import Image as PilImage  # noqa: PLC0415

            buf = io.BytesIO()
            with PilImage.open(path) as img:
                img.convert("RGB").save(buf, format="JPEG", quality=90)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            LOGGER.exception("Failed to convert HEIC to JPEG: %s", path)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class _ImageServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, port: int) -> None:
        super().__init__(("0.0.0.0", port), _ImageHandler)  # noqa: S104
        self.registry: dict[str, str] = {}


class ChromecastService:
    """Discover Chromecasts, serve images via HTTP, and control a slideshow."""

    def __init__(self, port: int = 8765) -> None:
        self._port = port
        self._server: _ImageServer | None = None
        self._server_thread: threading.Thread | None = None
        self._cast: Any = None
        self._browser: Any = None  # kept alive until after cast.wait()
        self._image_paths: list[str] = []
        self._keys: list[str] = []
        self._current_index: int = 0
        self._local_ip: str = _get_local_ip()
        self._start_http_server()

    def _start_http_server(self) -> None:
        try:
            self._server = _ImageServer(self._port)
            self._server_thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="chromecast-http"
            )
            self._server_thread.start()
            LOGGER.info("Chromecast image server on %s:%s", self._local_ip, self._port)
        except OSError:
            LOGGER.exception(
                "Failed to start Chromecast image HTTP server on port %s", self._port
            )

    def discover(self, timeout: float = 8.0) -> list[object]:
        """Discover Chromecasts on the LAN (blocking, run in a worker thread).

        The zeroconf browser is kept alive in self._browser so that cast.wait()
        can resolve the device address when connect() is called later.
        """
        import pychromecast  # noqa: PLC0415

        # Stop any previous browser before starting a new scan
        self._stop_browser()
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        self._browser = browser  # do NOT stop it yet — needed by cast.wait()
        return list(chromecasts)

    def _stop_browser(self) -> None:
        if self._browser is not None:
            try:
                import pychromecast  # noqa: PLC0415

                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:  # noqa: BLE001
                LOGGER.debug("Error stopping zeroconf browser", exc_info=True)
            self._browser = None

    def connect(self, cast: Any) -> None:  # noqa: ANN401
        """Connect to a Chromecast device and wait for it to be ready."""
        self._cast = cast
        cast.wait()  # needs zeroconf browser alive
        # Safe to stop the browser now that the connection is established
        self._stop_browser()
        LOGGER.info("Connected to %s", getattr(cast, "name", cast))

    def start_slideshow(self, image_paths: list[str]) -> None:
        """Register images and cast the first one. Skips video files."""
        still_paths = [
            p for p in image_paths if Path(p).suffix.lower() not in _VIDEO_SUFFIXES
        ]
        if not still_paths:
            return
        if self._server is None:
            return
        self._server.registry.clear()
        self._image_paths = still_paths
        self._keys = []
        for path in still_paths:
            key = uuid.uuid4().hex
            self._keys.append(key)
            self._server.registry[key] = path
        self._current_index = 0
        self._cast_current()

    def _cast_current(self) -> None:
        if self._cast is None or not self._image_paths:
            return
        key = self._keys[self._current_index]
        path = self._image_paths[self._current_index]
        url = f"http://{self._local_ip}:{self._port}/cast/{key}"
        suffix = Path(path).suffix.lower()
        # HEIC is converted to JPEG by _serve_heic_as_jpeg, so tell Chromecast image/jpeg
        mime = (
            "image/jpeg"
            if suffix in _HEIC_SUFFIXES
            else mimetypes.types_map.get(suffix, "image/jpeg")
        )
        LOGGER.debug("Casting %s (%s)", url, mime)
        mc = self._cast.media_controller
        mc.play_media(url, mime)

    def next(self) -> None:
        if not self._image_paths:
            return
        self._current_index = (self._current_index + 1) % len(self._image_paths)
        self._cast_current()

    def prev(self) -> None:
        if not self._image_paths:
            return
        self._current_index = (self._current_index - 1) % len(self._image_paths)
        self._cast_current()

    def stop(self) -> None:
        """Stop slideshow and disconnect from Chromecast."""
        self._stop_browser()
        if self._cast is not None:
            try:
                self._cast.quit_app()
            except Exception:
                LOGGER.exception("Error stopping Chromecast")
            self._cast = None
        if self._server is not None:
            self._server.registry.clear()
        self._image_paths = []
        self._keys = []

    def shutdown(self) -> None:
        """Shut down the HTTP server on app exit."""
        self._stop_browser()
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def total(self) -> int:
        return len(self._image_paths)

    @property
    def current_path(self) -> str | None:
        if not self._image_paths:
            return None
        return self._image_paths[self._current_index]

    @property
    def is_connected(self) -> bool:
        return self._cast is not None
