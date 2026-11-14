"""CPU-optimized YOLO nano person detector with OpenVINO export and motion gate."""

from __future__ import annotations

import logging
import time
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
class Detection:
    """Single detected person bounding box in pixel coordinates."""

    bbox: tuple[int, int, int, int]
    confidence: float
    class_id: int = PERSON_CLASS_ID
    class_name: str = PERSON_CLASS_NAME


@dataclass(frozen=True)
class DetectionResult:
    """Output of processing a single frame through the motion gate and detector."""

    detections: list[Detection]
    ran_inference: bool
    motion_detected: bool
    motion_ratio: float
    inference_ms: float
    skipped: bool
    backend: str


class MotionGate:
    """Lightweight MOG2 background-subtraction gate to avoid idle YOLO inference."""

    def __init__(
        self,
        *,
        threshold: float = 0.005,
        var_threshold: float = 25.0,
        history: int = 300,
        downscale_width: int = 320,
        hold_seconds: float = 0.5,
        warmup_frames: int = 2,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = max(0.0, threshold)
        self._downscale_width = max(64, downscale_width)
        self._hold_seconds = max(0.0, hold_seconds)
        self._warmup_frames = max(0, warmup_frames)
        self._monotonic = monotonic_fn

        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=max(10, history),
            varThreshold=max(1.0, var_threshold),
            detectShadows=False,
        )
        self._kernel = np.ones((3, 3), dtype=np.uint8)
        self._frames_seen = 0
        self._last_motion_time: float = 0.0

    def check(self, frame: np.ndarray) -> tuple[bool, float]:
        """Return (motion_active, foreground_ratio) for the given BGR frame."""
        if frame is None or frame.size == 0:
            return False, 0.0

        self._frames_seen += 1
        height, width = frame.shape[:2]
        if width > self._downscale_width:
            scale = self._downscale_width / float(width)
            target_h = max(1, int(round(height * scale)))
            small = cv2.resize(
                frame,
                (self._downscale_width, target_h),
                interpolation=cv2.INTER_AREA,
            )
        else:
            small = frame

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        fg_mask = self._subtractor.apply(blurred)
        _, binary = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, self._kernel)

        changed_pixels = int(cv2.countNonZero(cleaned))
        motion_ratio = changed_pixels / float(cleaned.size)

        now = self._monotonic()
        if self._frames_seen <= self._warmup_frames:
            self._last_motion_time = now
            return True, motion_ratio

        if motion_ratio >= self._threshold:
            self._last_motion_time = now
            return True, motion_ratio

        if (
            self._hold_seconds > 0.0
            and self._last_motion_time > 0.0
            and (now - self._last_motion_time) <= self._hold_seconds
        ):
            return True, motion_ratio

        return False, motion_ratio


def _default_yolo_loader(model_source: str, task: str = "detect") -> Any:
    from ultralytics import YOLO

    return YOLO(model_source, task=task)


class PersonDetector:
    """Person-only YOLO nano detector with OpenVINO CPU export, motion gate, and FPS cap."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        model_path: Optional[str | Path] = None,
        confidence: Optional[float] = None,
        imgsz: Optional[int] = None,
        target_fps: Optional[float] = None,
        frame_skip: Optional[int] = None,
        use_openvino: Optional[bool] = None,
        motion_gate_enabled: Optional[bool] = None,
        motion_threshold: Optional[float] = None,
        motion_hold_seconds: Optional[float] = None,
        model_loader: Optional[Callable[[str, str], Any]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
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
        self._target_fps = (
            cfg.detection_target_fps if target_fps is None else float(target_fps)
        )
        self._frame_skip = (
            cfg.detection_frame_skip if frame_skip is None else int(frame_skip)
        )
        self._use_openvino = (
            cfg.detection_use_openvino if use_openvino is None else bool(use_openvino)
        )
        self._motion_gate_enabled = (
            cfg.motion_gate_enabled
            if motion_gate_enabled is None
            else bool(motion_gate_enabled)
        )
        self._monotonic = monotonic_fn
        self._model_loader = model_loader or _default_yolo_loader

        self._motion_gate = MotionGate(
            threshold=(
                cfg.motion_threshold
                if motion_threshold is None
                else float(motion_threshold)
            ),
            var_threshold=cfg.motion_var_threshold,
            history=cfg.motion_history,
            downscale_width=cfg.motion_downscale_width,
            hold_seconds=(
                cfg.motion_hold_seconds
                if motion_hold_seconds is None
                else float(motion_hold_seconds)
            ),
            warmup_frames=cfg.motion_warmup_frames,
            monotonic_fn=monotonic_fn,
        )

        self._backend = "uninitialized"
        self._model = self._load_model()

        self._frame_counter = 0
        self._last_process_time = 0.0
        self._last_detections: list[Detection] = []

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def confidence(self) -> float:
        return self._confidence

    @property
    def imgsz(self) -> int:
        return self._imgsz

    @property
    def target_fps(self) -> float:
        return self._target_fps

    @property
    def motion_gate_enabled(self) -> bool:
        return self._motion_gate_enabled

    def _openvino_dir(self) -> Path:
        return self._model_path.parent / f"{self._model_path.stem}_openvino_model"

    def _load_model(self) -> Any:
        if self._model_path.parent:
            self._model_path.parent.mkdir(parents=True, exist_ok=True)

        ov_dir = self._openvino_dir()
        if self._use_openvino:
            if (ov_dir / "openvino_model.xml").exists():
                try:
                    logger.info("Loading cached OpenVINO model: %s", ov_dir)
                    model = self._model_loader(str(ov_dir), "detect")
                    self._backend = "openvino"
                    return model
                except Exception as exc:
                    logger.warning(
                        "Failed to load OpenVINO model (%s); retrying export/PyTorch fallback.",
                        exc,
                    )

            pt_model = self._model_loader(str(self._model_path), "detect")
            try:
                logger.info(
                    "Exporting %s to OpenVINO (imgsz=%d)...",
                    self._model_path.name,
                    self._imgsz,
                )
                exported = pt_model.export(
                    format="openvino",
                    imgsz=self._imgsz,
                    half=False,
                )
                ov_target = Path(str(exported)) if exported else ov_dir
                ov_model = self._model_loader(str(ov_target), "detect")
                self._backend = "openvino"
                logger.info("OpenVINO export ready: %s", ov_target)
                return ov_model
            except Exception as exc:
                logger.warning(
                    "OpenVINO export failed (%s); falling back to PyTorch CPU inference.",
                    exc,
                )
                self._backend = "pytorch"
                return pt_model

        model = self._model_loader(str(self._model_path), "detect")
        self._backend = "pytorch"
        logger.info("Loaded PyTorch CPU model: %s", self._model_path)
        return model

    def _should_process_frame(self, now: float) -> bool:
        self._frame_counter += 1
        if self._frame_skip > 0:
            return (self._frame_counter - 1) % (self._frame_skip + 1) == 0

        if self._target_fps <= 0:
            return True
        min_interval = 1.0 / self._target_fps
        if self._last_process_time == 0.0 or (now - self._last_process_time) >= min_interval:
            return True
        return False

    def _extract_person_detections(self, results: Any) -> list[Detection]:
        detections: list[Detection] = []
        if not results:
            return detections

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            xyxy_list = boxes.xyxy.tolist() if hasattr(boxes.xyxy, "tolist") else list(boxes.xyxy)
            conf_list = boxes.conf.tolist() if hasattr(boxes.conf, "tolist") else list(boxes.conf)
            cls_list = boxes.cls.tolist() if hasattr(boxes.cls, "tolist") else list(boxes.cls)

            for coords, conf, cls_id in zip(xyxy_list, conf_list, cls_list):
                if int(cls_id) != PERSON_CLASS_ID:
                    continue
                conf_val = float(conf)
                if conf_val < self._confidence:
                    continue
                x1, y1, x2, y2 = (int(round(c)) for c in coords[:4])
                detections.append(
                    Detection(
                        bbox=(x1, y1, x2, y2),
                        confidence=conf_val,
                        class_id=PERSON_CLASS_ID,
                        class_name=PERSON_CLASS_NAME,
                    )
                )
        return detections

    def detect(self, frame: np.ndarray, *, force: bool = False) -> DetectionResult:
        """Run frame-rate-limited, motion-gated person detection on a BGR frame."""
        if frame is None or frame.size == 0:
            return DetectionResult(
                detections=[],
                ran_inference=False,
                motion_detected=False,
                motion_ratio=0.0,
                inference_ms=0.0,
                skipped=True,
                backend=self._backend,
            )

        now = self._monotonic()
        if not force and not self._should_process_frame(now):
            return DetectionResult(
                detections=list(self._last_detections),
                ran_inference=False,
                motion_detected=False,
                motion_ratio=0.0,
                inference_ms=0.0,
                skipped=True,
                backend=self._backend,
            )

        self._last_process_time = now

        motion_detected = True
        motion_ratio = 1.0
        if self._motion_gate_enabled and not force:
            motion_detected, motion_ratio = self._motion_gate.check(frame)
            if not motion_detected:
                self._last_detections = []
                return DetectionResult(
                    detections=[],
                    ran_inference=False,
                    motion_detected=False,
                    motion_ratio=motion_ratio,
                    inference_ms=0.0,
                    skipped=False,
                    backend=self._backend,
                )

        t0 = self._monotonic()
        results = self._model.predict(
            source=frame,
            classes=[PERSON_CLASS_ID],
            conf=self._confidence,
            imgsz=self._imgsz,
            device="cpu",
            verbose=False,
        )
        inference_ms = (self._monotonic() - t0) * 1000.0

        detections = self._extract_person_detections(results)
        self._last_detections = detections

        return DetectionResult(
            detections=list(detections),
            ran_inference=True,
            motion_detected=motion_detected,
            motion_ratio=motion_ratio,
            inference_ms=inference_ms,
            skipped=False,
            backend=self._backend,
        )


def draw_detections(
    frame: np.ndarray,
    detections: Iterable[Detection],
    *,
    color: tuple[int, int, int] = (0, 220, 0),
    thickness: int = 2,
) -> np.ndarray:
    """Draw person bounding boxes and confidence scores onto a copy of the frame."""
    annotated = frame.copy()
    height, width = annotated.shape[:2]

    for det in detections:
        x1, y1, x2, y2 = det.bbox
        x1 = max(0, min(width - 1, x1))
        y1 = max(0, min(height - 1, y1))
        x2 = max(0, min(width - 1, x2))
        y2 = max(0, min(height - 1, y2))

        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)
        label = f"{det.class_name} {det.confidence:.2f}"
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
