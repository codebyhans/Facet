from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.request import urlopen

if TYPE_CHECKING:
    from collections.abc import Callable


class ModelDownloadError(RuntimeError):
    """Raised when model download fails."""


class ModelSetupService:
    """Handles fetching and validating local ONNX model files."""

    _DEFAULT_MODEL_URLS = (
        "https://huggingface.co/immich-app/antelopev2/resolve/main/recognition/model.onnx",
        "https://huggingface.co/immich-app/antelopev2/resolve/main/glintr100.onnx",
    )
    _DEFAULT_MODEL_NAME = "recognition_model.onnx"

    def __init__(self, model_dir: Path) -> None:
        self._model_dir = Path(model_dir)
        self._model_dir.mkdir(parents=True, exist_ok=True)

    def default_model_path(self) -> Path:
        """Return local path for recommended ONNX model."""
        return self._model_dir / self._DEFAULT_MODEL_NAME

    def download_recommended_model(
        self,
        *,
        progress: Callable[[int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        """Download recommended model and return local path."""
        target = self.default_model_path()
        if target.exists():
            self._emit_progress(progress, 100)
            return target

        tmp_target = target.with_suffix(target.suffix + ".part")
        download_error: ModelDownloadError | None = None
        for url in self._DEFAULT_MODEL_URLS:
            self._validate_https_url(url)
            try:
                self._download_to_temp(
                    url=url,
                    tmp_target=tmp_target,
                    progress=progress,
                    cancelled=cancelled,
                )
                tmp_target.replace(target)
                download_error = None
                break
            except ModelDownloadError as exc:
                download_error = exc
                self._cleanup_partial(tmp_target)
            except OSError as exc:
                self._cleanup_partial(tmp_target)
                download_error = ModelDownloadError(f"Failed to download model: {exc}")

        if download_error is not None:
            raise download_error

        self._emit_progress(progress, 100)
        return target

    def is_valid_onnx_path(self, path: Path | None) -> bool:
        """Return true if ONNX path exists and has expected extension."""
        if path is None:
            return False
        resolved = Path(path)
        return resolved.exists() and resolved.suffix.lower() == ".onnx"

    def _download_to_temp(
        self,
        *,
        url: str,
        tmp_target: Path,
        progress: Callable[[int], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        with urlopen(url) as response, tmp_target.open("wb") as out:  # noqa: S310
            length_header = response.headers.get("Content-Length")
            total = int(length_header) if length_header is not None else 0
            received = 0

            while True:
                if cancelled is not None and cancelled():
                    self._raise_cancelled()
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if total > 0:
                    pct = int((received / total) * 100)
                    self._emit_progress(progress, min(100, max(0, pct)))

    def _validate_https_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            msg = "Model URL must use https"
            raise ModelDownloadError(msg)

    def _cleanup_partial(self, tmp_target: Path) -> None:
        if tmp_target.exists():
            tmp_target.unlink(missing_ok=True)

    def _emit_progress(
        self,
        progress: Callable[[int], None] | None,
        value: int,
    ) -> None:
        if progress is not None:
            progress(value)

    def _raise_cancelled(self) -> None:
        msg = "Model download cancelled"
        raise ModelDownloadError(msg)
