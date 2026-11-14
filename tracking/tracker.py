"""ByteTrack person tracker built on Ultralytics YOLO."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import cv2
import numpy as np

from config.settings import BASE_DIR, Settings, get_settings

logger = logging.getLogger(__name__)

PERSON_CLASS_ID = 0
PERSON_CLASS_NAME = "person"


@dataclass(frozen=True)
class TrackedPerson:
    """A tracked person detection with a stable ByteTrack ID."""

    track_id: int
    bbox: tuple[int, int, int, int]
    confidence: float
    class_id: int = PERSON_CLASS_ID
    class_name: str = PERSON_CLASS_NAME

    @property
    def bottom_center(self) -> tuple[int, int]:
        """Return the bottom-centre (x, y) anchor point of the bounding box."""
        x1, _, x2, y2 = self.bbox
        return (int(round((x1 + x2) / 2.0)), int(y2))


def _default_yolo_loader(model_source: str, task: str = "detect") -> Any:
    from ultralytics import YOLO

    return YOLO(model_source, task=task)


class PersonTracker:
    """Assigns stable ByteTrack IDs to detected people across frames."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        model_path: Optional[str | Path] = None,
        confidence: Optional[float] = None,
        imgsz: Optional[int] = None,
        use_openvino: Optional[bool] = None,
        tracker_config: str = "bytetrack.yaml",
        model_loader: Optional[Callable[[str, str], Any]] = None,
    ) -> None:
        cfg = settings or get_settings()

        raw_model = str(model_path if model_path is not None else cfg.detection_model)
        resolved_model = Path(raw_model)
        if not resolved_model.is_absolute():
            resolved_model = BASE_DIR / resolved_model
        self._model_path = resolved_model

        self._confidence = (
            cfg.detection_confidence if confidence is None else float(confidence)
        )
        self._imgsz = cfg.detection_imgsz if imgsz is None else int(imgsz)
        self._use_openvino = (
            cfg.detection_use_openvino if use_openvino is None else bool(use_openvino)
        )
        self._tracker_config = tracker_config
        self._model_loader = model_loader or _default_yolo_loader

        self._backend = "uninitialized"
        self._model = self._load_model()

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def tracker_config(self) -> str:
        return self._tracker_config

    def _openvino_dir(self) -> Path:
        return self._model_path.parent / f"{self._model_path.stem}_openvino_model"

    def _load_model(self) -> Any:
        if self._model_path.parent:
            self._model_path.parent.mkdir(parents=True, exist_ok=True)

        ov_dir = self._openvino_dir()
        if self._use_openvino:
            if (ov_dir / "openvino_model.xml").exists():
                try:
                    model = self._model_loader(str(ov_dir), "detect")
                    self._backend = "openvino"
                    return model
                except Exception as exc:
                    logger.warning(
                        "Failed to load OpenVINO tracker model (%s); retrying fallback.",
                        exc,
                    )

            pt_model = self._model_loader(str(self._model_path), "detect")
            try:
                exported = pt_model.export(
                    format="openvino",
                    imgsz=self._imgsz,
                    half=False,
                )
                ov_target = Path(str(exported)) if exported else ov_dir
                ov_model = self._model_loader(str(ov_target), "detect")
                self._backend = "openvino"
                return ov_model
            except Exception as exc:
                logger.warning(
                    "OpenVINO export failed (%s); falling back to PyTorch CPU tracking.",
                    exc,
                )
                self._backend = "pytorch"
                return pt_model

        model = self._model_loader(str(self._model_path), "detect")
        self._backend = "pytorch"
        return model

    def _extract_tracks(self, results: Any) -> list[TrackedPerson]:
        tracks: list[TrackedPerson] = []
        if not results:
            return tracks

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or getattr(boxes, "id", None) is None:
                continue

            xyxy_list = (
                boxes.xyxy.tolist() if hasattr(boxes.xyxy, "tolist") else list(boxes.xyxy)
            )
            conf_list = (
                boxes.conf.tolist() if hasattr(boxes.conf, "tolist") else list(boxes.conf)
            )
            cls_list = (
                boxes.cls.tolist() if hasattr(boxes.cls, "tolist") else list(boxes.cls)
            )
            id_list = (
                boxes.id.tolist() if hasattr(boxes.id, "tolist") else list(boxes.id)
            )

            for coords, conf, cls_id, raw_id in zip(
                xyxy_list, conf_list, cls_list, id_list
            ):
                if raw_id is None or int(cls_id) != PERSON_CLASS_ID:
                    continue
                conf_val = float(conf)
                if conf_val < self._confidence:
                    continue
                x1, y1, x2, y2 = (int(round(c)) for c in coords[:4])
                tracks.append(
                    TrackedPerson(
                        track_id=int(raw_id),
                        bbox=(x1, y1, x2, y2),
                        confidence=conf_val,
                        class_id=PERSON_CLASS_ID,
                        class_name=PERSON_CLASS_NAME,
                    )
                )
        return tracks

    def update(self, frame: np.ndarray) -> list[TrackedPerson]:
        """Run detection + ByteTrack association and return tracked persons."""
        if frame is None or frame.size == 0:
            return []

        results = self._model.track(
            source=frame,
            tracker=self._tracker_config,
            persist=True,
            classes=[PERSON_CLASS_ID],
            conf=self._confidence,
            imgsz=self._imgsz,
            device="cpu",
            verbose=False,
        )
        return self._extract_tracks(results)

    def reset(self) -> None:
        """Reset Ultralytics internal tracker state when starting a new stream."""
        predictor = getattr(self._model, "predictor", None)
        if predictor is not None and hasattr(predictor, "trackers"):
            for tracker in getattr(predictor, "trackers", []):
                if hasattr(tracker, "reset"):
                    tracker.reset()


def draw_tracks(
    frame: np.ndarray,
    tracks: Iterable[TrackedPerson],
    *,
    color: tuple[int, int, int] = (0, 220, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Draw bounding boxes, stable track IDs, and bottom-centre anchor points."""
    annotated = frame.copy()
    height, width = annotated.shape[:2]

    for track in tracks:
        x1, y1, x2, y2 = track.bbox
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))

        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

        ax, ay = track.bottom_center
        ax = max(0, min(width - 1, ax))
        ay = max(0, min(height - 1, ay))
        cv2.circle(annotated, (ax, ay), 4, (0, 255, 255), thickness=-1)

        label = f"ID #{track.track_id} ({track.confidence:.2f})"
        (label_w, label_h), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1
        )
        top_y = max(label_h + baseline + 6, y1)
        cv2.rectangle(
            annotated,
            (x1, top_y - label_h - baseline - 6),
            (min(width - 1, x1 + label_w + 8), top_y),
            color,
            thickness=-1,
        )
        cv2.putText(
            annotated,
            label,
            (x1 + 4, top_y - baseline - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    return annotated
