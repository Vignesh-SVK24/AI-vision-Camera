"""File-based video source for testing, simulation, and offline playback with automatic sample clip generation."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from camera.base import CameraSource, CameraStatus

logger = logging.getLogger(__name__)


def generate_synthetic_test_clip(
    output_path: Path,
    width: int = 640,
    height: int = 480,
    fps: int = 20,
    duration_seconds: int = 3,
) -> Path:
    """Generate a clean synthetic video file with moving shapes for offline fallback testing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
    total_frames = fps * duration_seconds

    for i in range(total_frames):
        canvas = np.zeros((height, width, 3), dtype=np.uint8)
        # Background gradient
        canvas[:, :] = (35, 30, 25)
        # Moving synthetic target
        cx = int((width / 4) + (width / 3) * (0.5 + 0.5 * np.sin(2 * np.pi * i / total_frames)))
        cy = int((height / 3) + (height / 4) * (0.5 + 0.5 * np.cos(2 * np.pi * i / total_frames)))
        cv2.circle(canvas, (cx, cy), 35, (40, 180, 220), -1)
        cv2.putText(
            canvas,
            f"TEST CLIP FRAME {i:03d}",
            (30, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (240, 240, 240),
            2,
            cv2.LINE_AA,
        )
        writer.write(canvas)

    writer.release()
    logger.info("Generated synthetic test clip at: %s", output_path)
    return output_path


class FileSource(CameraSource):
    """File video playback source reading recorded video files with loop support."""

    def __init__(
        self,
        file_path: str | Path,
        *,
        loop: bool = True,
        target_fps: Optional[float] = None,
        downscale_width: int = 640,
        capture_factory: Optional[Callable[[str], Any]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(downscale_width=downscale_width)
        self.file_path = Path(file_path).resolve()
        self.loop = loop
        self.target_fps = target_fps
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._on_event = on_event

        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if self._on_event:
            self.on_status_change(self._handle_status_change)

    def _handle_status_change(self, new_status: CameraStatus, metadata: dict[str, Any]) -> None:
        if not self._on_event:
            return
        if new_status == CameraStatus.CONNECTED:
            self._on_event("camera_online", {"source": self.masked_source, "status": "connected"})
        elif new_status == CameraStatus.DISCONNECTED:
            self._on_event("camera_offline", {"source": self.masked_source, "status": "disconnected"})

    @property
    def masked_source(self) -> str:
        return f"file:{self.file_path.name}"

    def _ensure_file_exists(self) -> Path:
        if not self.file_path.is_file():
            logger.info("Video file %s not found. Creating synthetic fallback clip.", self.file_path)
            return generate_synthetic_test_clip(self.file_path)
        return self.file_path

    def start(self) -> FileSource:
        if self._thread and self._thread.is_alive():
            return self

        self._running.set()
        self._set_status(CameraStatus.CONNECTING)

        self._thread = threading.Thread(
            target=self._reader_loop,
            name=f"FileReader-{self.file_path.name}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        self._set_status(CameraStatus.DISCONNECTED)
        logger.info("FileSource stopped: %s", self.masked_source)

    def _reader_loop(self) -> None:
        actual_path = self._ensure_file_exists()

        while self._running.is_set():
            cap = self._capture_factory(str(actual_path))
            if cap is None or not cap.isOpened():
                logger.error("Unable to open video file: %s", actual_path)
                self._set_status(CameraStatus.DISCONNECTED)
                time.sleep(1.0)
                if not self.loop:
                    break
                continue

            # Determine frame pacing
            file_fps = cap.get(cv2.CAP_PROP_FPS) if hasattr(cap, "get") else 0.0
            playback_fps = self.target_fps or (file_fps if file_fps and file_fps > 5.0 else 30.0)
            frame_delay = 1.0 / max(1.0, float(playback_fps))

            self._set_status(CameraStatus.CONNECTED)
            logger.info("Playback started for %s at %.1f FPS", self.masked_source, playback_fps)

            while self._running.is_set():
                t0 = time.perf_counter()
                ok, frame = cap.read()

                if not ok or frame is None:
                    if self.loop and self._running.is_set():
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break

                self._update_frame(frame, calculated_fps=playback_fps)

                # Maintain realistic playback timing
                elapsed = time.perf_counter() - t0
                sleep_time = frame_delay - elapsed
                if sleep_time > 0.001:
                    time.sleep(sleep_time)

            try:
                cap.release()
            except Exception:
                pass

            if not self.loop:
                break

        self._set_status(CameraStatus.DISCONNECTED)
