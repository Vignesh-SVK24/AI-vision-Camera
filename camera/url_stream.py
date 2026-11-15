"""URL / RTSP camera stream source supporting TCP transport, credential masking, and auto-reconnect."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

import cv2

from camera.base import CameraSource, CameraStatus, mask_url_credentials
from camera.health import CameraHealthWatchdog, ReconnectBackoff

logger = logging.getLogger(__name__)


def ensure_rtsp_tcp_transport() -> str:
    """Set OPENCV_FFMPEG_CAPTURE_OPTIONS to force TCP transport for RTSP streams."""
    current = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")
    parts = [p.strip() for p in current.split("|") if p.strip()]
    filtered = [p for p in parts if not p.startswith("rtsp_transport;")]
    filtered.insert(0, "rtsp_transport;tcp")
    updated = "|".join(filtered)
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = updated
    return updated


class URLStreamSource(CameraSource):
    """Network camera source reading HTTP or RTSP streams over TCP with credential masking."""

    def __init__(
        self,
        url: str,
        *,
        downscale_width: int = 640,
        stale_timeout: float = 8.0,
        reconnect_initial_delay: float = 1.0,
        reconnect_max_delay: float = 30.0,
        rtsp_transport: str = "tcp",
        capture_factory: Optional[Callable[[str], Any]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        super().__init__(downscale_width=downscale_width, stale_timeout=stale_timeout)
        self.raw_url = str(url or "").strip()
        self.rtsp_transport = rtsp_transport
        self._masked_source = mask_url_credentials(self.raw_url)
        self._capture_factory = capture_factory or cv2.VideoCapture

        self._cap: Optional[Any] = None
        self._running = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backoff = ReconnectBackoff(
            base_delay=reconnect_initial_delay,
            max_delay=reconnect_max_delay,
            jitter_range=(0.01, 0.05) if reconnect_initial_delay < 0.2 else (0.1, 0.8),
        )
        self._attempt = 0

        self._watchdog = CameraHealthWatchdog(
            source=self,
            stale_timeout=self._stale_timeout,
            on_stale=self._trigger_reconnect,
            on_event=on_event,
        )

    @property
    def masked_source(self) -> str:
        return self._masked_source

    def _trigger_reconnect(self, _source: CameraSource) -> None:
        self._reconnect_event.set()
        cap_to_release = self._cap
        if cap_to_release is not None:
            try:
                cap_to_release.release()
            except Exception:
                pass

    def _log_diagnostic_help(self) -> None:
        logger.error(
            "URL/RTSP stream failed to connect: %s\n"
            "Likely causes and troubleshooting steps:\n"
            "  1. Network or Wi-Fi disconnection: verify camera device IP is reachable via ping.\n"
            "  2. Invalid URL, port, or path: verify RTSP/HTTP path in .env (e.g. rtsp://<ip>:554/stream1).\n"
            "  3. Authentication error: verify username and password credentials are correct.\n"
            "  4. Firewall blocking RTSP port 554: check router and host firewall settings.",
            self.masked_source,
        )

    def _open_capture(self) -> Optional[Any]:
        if self.raw_url.lower().startswith("rtsp://"):
            ensure_rtsp_tcp_transport()
        try:
            cap = self._capture_factory(self.raw_url)
        except Exception as exc:
            logger.warning("Exception initializing stream (%s): %s", self.masked_source, exc)
            return None

        if cap is None or not cap.isOpened():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            return None
        return cap

    def start(self) -> URLStreamSource:
        if self._thread and self._thread.is_alive():
            return self

        self._running.set()
        self._reconnect_event.clear()
        self._set_status(CameraStatus.CONNECTING)

        self._thread = threading.Thread(
            target=self._reader_loop,
            name=f"URLStreamReader-{self.masked_source}",
            daemon=True,
        )
        self._thread.start()
        self._watchdog.start()
        return self

    def stop(self) -> None:
        self._running.clear()
        self._reconnect_event.set()
        self._watchdog.stop()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None

        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

        self._set_status(CameraStatus.DISCONNECTED)
        logger.info("URLStreamSource stopped: %s", self.masked_source)

    def _reader_loop(self) -> None:
        while self._running.is_set():
            cap = self._open_capture()

            if cap is None:
                self._attempt += 1
                self._log_diagnostic_help()
                delay = self._backoff.get_delay(self._attempt)
                time.sleep(delay)
                continue

            self._cap = cap
            self._attempt = 0
            self._set_status(CameraStatus.CONNECTED)
            logger.info("URL stream connected: %s", self.masked_source)

            frame_count = 0
            t_start = time.perf_counter()
            rolling_fps = 0.0

            while self._running.is_set() and not self._reconnect_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    logger.warning("Stream read failure from %s", self.masked_source)
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

            was_stalled = self._reconnect_event.is_set()
            self._reconnect_event.clear()
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

            if self._running.is_set():
                if not was_stalled:
                    self._set_status(CameraStatus.DISCONNECTED, {"reason": "read_error"})
                self._attempt += 1
                delay = self._backoff.get_delay(self._attempt)
                logger.info("Reconnecting stream in %.1fs (attempt %d)...", delay, self._attempt)
                time.sleep(delay)
