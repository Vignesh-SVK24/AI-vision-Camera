"""On-demand shared live camera manager serving JPEG frames for the web dashboard."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

import cv2

from camera.factory import create_camera_source
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def encode_frame_jpeg(frame: Any, *, max_width: int = 960, quality: int = 75) -> bytes:
    """Resize large frames and encode as compact JPEG bytes for browser streaming."""
    target = frame
    height, width = frame.shape[:2]
    if max_width > 0 and width > max_width:
        scale = max_width / float(width)
        new_height = max(1, int(round(height * scale)))
        target = cv2.resize(frame, (max_width, new_height), interpolation=cv2.INTER_AREA)

    ok, encoded = cv2.imencode(
        ".jpg",
        target,
        [int(cv2.IMWRITE_JPEG_QUALITY), max(30, min(95, int(quality)))],
    )
    if not ok:
        raise RuntimeError("Failed to encode live camera frame as JPEG.")
    return encoded.tobytes()


class LiveCameraManager:
    """Reference-counted shared camera reader so web viewers share one capture handle."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        camera_factory: Optional[Callable[[Settings], Any]] = None,
        max_width: int = 960,
        jpeg_quality: int = 75,
    ) -> None:
        self._settings = settings or get_settings()
        self._camera_factory = camera_factory
        self._max_width = max_width
        self._jpeg_quality = jpeg_quality

        self._lock = threading.Lock()
        self._camera: Any = None
        self._viewers = 0
        self._cached_seq = -1
        self._cached_jpeg: Optional[bytes] = None
        self._last_error: Optional[str] = None

    @property
    def viewer_count(self) -> int:
        with self._lock:
            return self._viewers

    def _build_camera(self) -> Any:
        if self._camera_factory is not None:
            return self._camera_factory(self._settings)
        # Allow slightly wider warm-up margin for Windows DirectShow webcam initialization.
        return create_camera_source(
            self._settings,
            stale_timeout=max(self._settings.rtsp_stale_timeout, 12.0),
        )

    def acquire(self) -> None:
        """Increment viewer refcount and start the shared camera if not already running."""
        with self._lock:
            self._viewers += 1
            if self._camera is not None:
                return
            try:
                cam = self._build_camera()
                cam.start()
                self._camera = cam
                self._cached_seq = -1
                self._cached_jpeg = None
                self._last_error = None
            except Exception as exc:
                self._viewers = max(0, self._viewers - 1)
                self._last_error = str(exc)
                logger.warning("Unable to start live web camera source: %s", exc)
                raise

    def release(self) -> None:
        """Decrement viewer refcount and stop the shared camera when no viewers remain."""
        cam_to_stop = None
        with self._lock:
            if self._viewers > 0:
                self._viewers -= 1
            if self._viewers == 0 and self._camera is not None:
                cam_to_stop = self._camera
                self._camera = None
                self._cached_seq = -1
                self._cached_jpeg = None
        if cam_to_stop is not None:
            try:
                cam_to_stop.stop()
            except Exception as exc:
                logger.debug("Error stopping live camera: %s", exc)

    def close(self) -> None:
        """Force-stop the shared camera during application shutdown."""
        cam_to_stop = None
        with self._lock:
            self._viewers = 0
            cam_to_stop = self._camera
            self._camera = None
            self._cached_seq = -1
            self._cached_jpeg = None
        if cam_to_stop is not None:
            try:
                cam_to_stop.stop()
            except Exception as exc:
                logger.debug("Error closing live camera: %s", exc)

    def get_state(self) -> dict[str, Any]:
        """Return current connection state, FPS, and any startup error."""
        with self._lock:
            cam = self._camera
            err = self._last_error

        if cam is None:
            return {
                "status": "disconnected",
                "fps": 0.0,
                "error": err,
            }

        connected = bool(getattr(cam, "is_connected", False))
        fps = float(getattr(cam, "fps", 0.0) or 0.0)
        return {
            "status": "connected" if connected else "connecting",
            "fps": round(fps, 1),
            "error": err,
        }

    def get_latest_jpeg(self, last_seq: int = -1) -> tuple[Optional[bytes], int]:
        """Return (jpeg_bytes, seq) when a newer frame is available, else (None, current_seq)."""
        with self._lock:
            cam = self._camera
            cached_seq = self._cached_seq
            cached_jpeg = self._cached_jpeg

        if cam is None:
            return None, last_seq

        ok, frame, seq = cam.read_with_seq(copy=False)
        if not ok or frame is None or seq == last_seq:
            return None, seq if ok else last_seq

        if seq == cached_seq and cached_jpeg is not None:
            return cached_jpeg, seq

        jpeg = encode_frame_jpeg(frame, max_width=self._max_width, quality=self._jpeg_quality)
        with self._lock:
            if self._camera is cam:
                self._cached_seq = seq
                self._cached_jpeg = jpeg
        return jpeg, seq

    def grab_snapshot_jpeg(self, *, wait_seconds: float = 10.0) -> Optional[bytes]:
        """Grab one live JPEG snapshot, temporarily acquiring the camera if needed."""
        self.acquire()
        try:
            deadline = time.monotonic() + max(0.2, float(wait_seconds))
            while time.monotonic() < deadline:
                jpeg, _ = self.get_latest_jpeg(last_seq=-1)
                if jpeg is not None:
                    return jpeg
                time.sleep(0.05)
            return None
        finally:
            self.release()
