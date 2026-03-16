"""Lightweight photo quality scoring without heavy ML models."""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class QualityScorer:
    """Estimate photo quality using lightweight heuristics."""

    @staticmethod
    def compute_quality_score(image_path: str) -> float:
        """
        Compute a quality score for an image (0.0 to 1.0).

        Combines:
        - Sharpness (Laplacian variance)
        - Brightness (exposure proxy)
        - Saturation (color balance)
        - Contrast (histogram range)

        Args:
            image_path: Path to image file

        Returns:
            Quality score from 0.0 (poor) to 1.0 (excellent)
        """
        try:
            # Read image
            img = cv2.imread(image_path)
            if img is None:
                logger.warning(f"Failed to read image: {image_path}")
                return 0.5

            # Convert to grayscale for sharpness
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            # Score 1: Sharpness (Laplacian variance)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            sharpness_score = min(1.0, laplacian_var / 100.0)  # Normalize

            # Score 2: Brightness/Exposure
            mean_brightness = np.mean(gray)
            # Optimal brightness around 127 (50% of 255)
            brightness_score = 1.0 - abs(mean_brightness - 127.0) / 127.0

            # Score 3: Saturation (color balance)
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
            saturation = hsv[:, :, 1]
            mean_saturation = np.mean(saturation) / 255.0
            # Prefer well-saturated images (0.3-0.8 range)
            saturation_score = min(1.0, max(0.0, (mean_saturation - 0.2) / 0.6))

            # Score 4: Contrast (histogram spread)
            hist = np.histogram(gray, bins=256, range=(0, 256))[0]
            # Entropy as proxy for contrast
            hist_normalized = hist / hist.sum()
            entropy = -np.sum(hist_normalized * np.log2(hist_normalized + 1e-10))
            contrast_score = min(1.0, entropy / 8.0)  # Max entropy ≈ 8 for 256 bins

            # Combine scores with weights
            quality = (
                sharpness_score * 0.35
                + brightness_score * 0.25
                + saturation_score * 0.20
                + contrast_score * 0.20
            )

            logger.debug(
                f"Image {image_path}: "
                f"sharpness={sharpness_score:.2f}, "
                f"brightness={brightness_score:.2f}, "
                f"saturation={saturation_score:.2f}, "
                f"contrast={contrast_score:.2f}, "
                f"quality={quality:.2f}"
            )

            return float(np.clip(quality, 0.0, 1.0))

        except Exception as e:
            logger.error(f"Error computing quality score for {image_path}: {e}")
            return 0.5  # Default to neutral
