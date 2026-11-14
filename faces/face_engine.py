"""OpenCV YuNet face detection and SFace embedding extraction (local CPU only)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

MODEL_NAME_SFACE = "sface_2021dec"


@dataclass(frozen=True)
class DetectedFace:
    """Detected face bounding box, detection confidence, and raw YuNet row."""

    bbox: tuple[int, int, int, int]  # (x, y, w, h)
    score: float
    raw_face: np.ndarray


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    """Return an L2-normalized 1-D float32 embedding vector."""
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm <= 1e-12:
        return arr
    return (arr / norm).astype(np.float32)


def compute_blur_score(image: np.ndarray, bbox: Optional[tuple[int, int, int, int]] = None) -> float:
    """Compute Laplacian variance as a sharpness metric (higher = sharper)."""
    if image is None or image.size == 0:
        return 0.0

    region = image
    if bbox is not None:
        h_img, w_img = image.shape[:2]
        x, y, w, h = bbox
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(w_img, x + w)
        y2 = min(h_img, y + h)
        if x2 <= x1 or y2 <= y1:
            return 0.0
        region = image[y1:y2, x1:x2]

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class FaceEngine:
    """Wraps cv2.FaceDetectorYN (YuNet) and cv2.FaceRecognizerSF (SFace)."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        yunet_model_path: Optional[Path | str] = None,
        sface_model_path: Optional[Path | str] = None,
        score_threshold: float = 0.75,
        nms_threshold: float = 0.3,
        top_k: int = 50,
    ) -> None:
        cfg = settings or get_settings()
        self._yunet_path = (
            Path(yunet_model_path) if yunet_model_path is not None else cfg.face_yunet_model
        )
        self._sface_path = (
            Path(sface_model_path) if sface_model_path is not None else cfg.face_sface_model
        )
        self._score_threshold = float(score_threshold)
        self._nms_threshold = float(nms_threshold)
        self._top_k = int(top_k)

        self._detector = None
        self._recognizer = None

    def _ensure_loaded(self, width: int, height: int) -> None:
        if self._detector is None:
            if not self._yunet_path.exists():
                raise FileNotFoundError(
                    f"YuNet model not found at {self._yunet_path}. "
                    "Run `python tools/download_models.py` first."
                )
            self._detector = cv2.FaceDetectorYN.create(
                str(self._yunet_path),
                "",
                (max(1, width), max(1, height)),
                self._score_threshold,
                self._nms_threshold,
                self._top_k,
            )

        if self._recognizer is None:
            if not self._sface_path.exists():
                raise FileNotFoundError(
                    f"SFace model not found at {self._sface_path}. "
                    "Run `python tools/download_models.py` first."
                )
            self._recognizer = cv2.FaceRecognizerSF.create(str(self._sface_path), "")

    def detect_faces(self, image: np.ndarray) -> list[DetectedFace]:
        """Detect faces in a BGR image using YuNet."""
        if image is None or image.size == 0:
            return []

        height, width = image.shape[:2]
        self._ensure_loaded(width, height)
        self._detector.setInputSize((width, height))
        _, raw_faces = self._detector.detect(image)
        if raw_faces is None or len(raw_faces) == 0:
            return []

        faces: list[DetectedFace] = []
        for row in raw_faces:
            x, y, w, h = (int(round(v)) for v in row[:4])
            score = float(row[14]) if len(row) > 14 else 1.0
            faces.append(DetectedFace(bbox=(x, y, w, h), score=score, raw_face=row))
        return faces

    def extract_embedding(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """Align a detected face and extract its L2-normalized SFace embedding."""
        height, width = image.shape[:2]
        self._ensure_loaded(width, height)
        aligned = self._recognizer.alignCrop(image, face.raw_face)
        feature = self._recognizer.feature(aligned)
        return l2_normalize(feature)
