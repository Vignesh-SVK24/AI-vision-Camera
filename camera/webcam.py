"""Webcam camera source supporting Windows DirectShow, Media Foundation, warmup, and diagnostics."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

import cv2

from camera.base import CameraSource, CameraStatus
from camera.health import CameraHealthWatchdog, ReconnectBackoff

logger = logging.getLogger(__name__)


def parse_webcam_source(source: Any) -> Optional[int]:
    """Parse integer camera index from 'webcam', 'webcam:0', '1', or integer."""
    if source is None:
        return None
    if isinstance(source, int):
        return source
    text = str(source).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    lower = text.lower()
    if lower == "webcam":
        return 0
    if lower.startswith("webcam:"):
        rest = lower.split(":", 1)[1].strip()
        if rest.isdigit():
            return int(rest)
    return None


def default_capture_factory(
    source: Any,
    backend_flag: Optional[int] = None,
    *,
    use_rtsp: bool = False,
) -> Any:
    """Default OpenCV VideoCapture creation helper supporting DSHOW and RTSP TCP transport."""
    if use_rtsp or (isinstance(source, str) and str(source).lower().startswith("rtsp://")):
        from camera.url_stream import ensure_rtsp_tcp_transport
        ensure_rtsp_tcp_transport()
    idx = parse_webcam_source(source)
    if idx is not None:
        flag = backend_flag if backend_flag is not None else getattr(cv2, "CAP_DSHOW", 700)
        cap = cv2.VideoCapture(idx, flag)
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap
    return cv2.VideoCapture(source)


def get_backend_flag(backend_name: str) -> int:
    """Map string backend identifier to OpenCV capture backend flag."""
    normalized = (backend_name or "").strip().lower()
    if normalized == "dshow":
        return getattr(cv2, "CAP_DSHOW", 700)
    if normalized == "msmf":
        return getattr(cv2, "CAP_MSMF", 1400)
    return 0


class WebcamSource(CameraSource):
    """Reliable local webcam reader supporting DSHOW/MSMF, warmup, and watchdog recovery."""

    def __init__(
        self,
        index: int = 0,
        backend: str = "dshow",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        downscale_width: int = 640,
        stale_timeout: float = 8.0,
        warmup_seconds: float = 5.0,
        capture_factory: Optional[Callable[[int, int], Any]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(downscale_width=downscale_width, stale_timeout=stale_timeout)
        self.index = int(index)
        self.backend = (backend or "dshow").lower()
        self.width = int(width)
        self.height = int(height)
        self.target_fps = int(fps)
        self.warmup_seconds = max(1.0, float(warmup_seconds))
        self._capture_factory = capture_factory or cv2.VideoCapture

        self._cap: Optional[Any] = None
        self._cap_lock = threading.Lock()
        self._running = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backoff = ReconnectBackoff(base_delay=1.0, max_delay=15.0)
        self._attempt = 0

        self._watchdog = CameraHealthWatchdog(
            source=self,
            stale_timeout=self._stale_timeout,
            on_stale=self._trigger_reconnect,
            on_event=on_event,
        )

    @property
    def masked_source(self) -> str:
        return f"webcam:{self.index} ({self.backend})"

    def _trigger_reconnect(self, _source: CameraSource) -> None:
        """Signal acquisition loop to cycle capture handle."""
        self._reconnect_event.set()

    def _log_diagnostic_help(self) -> None:
        """Emit detailed, actionable recovery instructions when webcam fails to open."""
        logger.error(
            "Webcam failure on index %d with backend '%s'.\n"
            "Likely causes and troubleshooting steps:\n"
            "  1. Another application is currently locking the camera (Teams, Zoom, Skype, Windows Camera app, browser).\n"
            "  2. Windows Privacy Settings: Open Settings -> Privacy & security -> Camera, and verify 'Let desktop apps access your camera' is ON.\n"
            "  3. Wrong camera index: Run 'python tools/list_cameras.py' to discover available indexes.\n"
            "  4. Incompatible backend: Try toggling WEBCAM_BACKEND between 'dshow' and 'msmf' in your .env configuration.",
            self.index,
            self.backend,
        )

    def _open_capture(self) -> Optional[Any]:
        """Attempt to open VideoCapture handle with configured backend and properties."""
        backend_flag = get_backend_flag(self.backend)
        try:
            if backend_flag:
                cap = self._capture_factory(self.index, backend_flag)
            else:
                cap = self._capture_factory(self.index)
        except Exception as exc:
            logger.warning("Exception initializing VideoCapture(%d, %s): %s", self.index, self.backend, exc)
            return None

        if cap is None or not cap.isOpened():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            return None

        # Apply desired stream resolution and frame rate
        try:
            if self.width > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
            if self.height > 0:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
            if self.target_fps > 0:
                cap.set(cv2.CAP_PROP_FPS, float(self.target_fps))
        except Exception as exc:
            logger.debug("Failed setting VideoCapture properties: %s", exc)

        return cap

    def _perform_warmup(self, cap: Any) -> bool:
        """Read initial frames until sensor warms up and produces valid pixels."""
        deadline = time.monotonic() + self.warmup_seconds
        while time.monotonic() < deadline and self._running.is_set():
            ok, frame = cap.read()
            if ok and frame is not None and frame.size > 0:
                self._update_frame(frame)
                return True
            time.sleep(0.05)
        return False

    def start(self) -> WebcamSource:
        """Launch reader and watchdog threads."""
        if self._thread and self._thread.is_alive():
            return self

        self._running.set()
        self._reconnect_event.clear()
        self._set_status(CameraStatus.CONNECTING)

        self._thread = threading.Thread(
            target=self._reader_loop,
            name=f"WebcamReader-{self.index}",
            daemon=True,
        )
        self._thread.start()
        self._watchdog.start()
        return self

    def stop(self) -> None:
        """Gracefully terminate threads and release hardware."""
        self._running.clear()
        self._reconnect_event.set()
        self._watchdog.stop()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

        with self._cap_lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

        self._set_status(CameraStatus.DISCONNECTED)
        logger.info("WebcamSource stopped: %s", self.masked_source)

    def _reader_loop(self) -> None:
        """Main reader loop keeping single newest frame with zero buffer lag."""
        while self._running.is_set():
            self._set_status(CameraStatus.CONNECTING, {"attempt": self._attempt})
            cap = self._open_capture()

            if cap is None:
                self._attempt += 1
                self._log_diagnostic_help()
                self._set_status(CameraStatus.DISCONNECTED, {"attempt": self._attempt})
                delay = self._backoff.get_delay(self._attempt)
                time.sleep(delay)
                continue

            # Warm up physical sensor
            if not self._perform_warmup(cap):
                logger.warning("Camera warmup timed out for %s", self.masked_source)
                try:
                    cap.release()
                except Exception:
                    pass
                self._attempt += 1
                delay = self._backoff.get_delay(self._attempt)
                time.sleep(delay)
                continue

            self._cap = cap
            self._attempt = 0
            self._set_status(CameraStatus.CONNECTED)
            logger.info("Webcam connected and capturing: %s", self.masked_source)

            # Rolling FPS counters
            frame_count = 0
            t_start = time.perf_counter()
            rolling_fps = 0.0

            while self._running.is_set() and not self._reconnect_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Frame read failure from %s", self.masked_source)
                    break

                frame_count += 1
                now = time.perf_counter()
                elapsed = now - t_start
                if elapsed >= 1.0:
                    rolling_fps = round(frame_count / elapsed, 1)
                    frame_count = 0
                    t_start = now

                packet = self._update_frame(frame, calculated_fps=rolling_fps)
                self._watchdog.mark_frame_received(packet.seq)

            # Clean up before reconnecting or exiting
            self._reconnect_event.clear()
            with self._cap_lock:
                cap_to_release = self._cap
                self._cap = None
            if cap_to_release is not None:
                try:
                    cap_to_release.release()
                except Exception:
                    pass

            if self._running.is_set():
                self._set_status(CameraStatus.DISCONNECTED)
                self._attempt += 1
                delay = self._backoff.get_delay(self._attempt)
                logger.info("Reconnecting webcam in %.1fs (attempt %d)...", delay, self._attempt)
                time.sleep(delay)
