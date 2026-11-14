"""Reliable RTSP and video-file camera client with automatic recovery."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from config.logging_config import mask_sensitive_text
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict[str, Any]], None]
CaptureFactory = Callable[[Any, bool], Any]


def build_rtsp_url(
    ip: str,
    username: str = "",
    password: str = "",
    stream: str = "stream1",
    port: int = 554,
) -> str:
    """Construct an RTSP URL with percent-encoded credentials."""
    clean_ip = ip.strip()
    if not clean_ip:
        raise ValueError("CAMERA_IP must not be empty when building an RTSP URL.")

    clean_stream = stream.strip().lstrip("/")
    if username and password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    elif username:
        auth = f"{quote(username, safe='')}@"
    else:
        auth = ""

    return f"rtsp://{auth}{clean_ip}:{port}/{clean_stream}"


def is_rtsp_source(source: Any) -> bool:
    """Return True if the source string represents an RTSP/RTSPS stream."""
    return isinstance(source, str) and source.strip().lower().startswith(
        ("rtsp://", "rtsps://")
    )


def ensure_rtsp_tcp_transport() -> str:
    """Ensure OPENCV_FFMPEG_CAPTURE_OPTIONS forces TCP transport for RTSP."""
    existing = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "").strip()
    if not existing:
        updated = "rtsp_transport;tcp"
    else:
        parts = [p for p in existing.split("|") if p and not p.startswith("rtsp_transport;")]
        parts.insert(0, "rtsp_transport;tcp")
        updated = "|".join(parts)
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = updated
    return updated


def parse_webcam_source(source: Any) -> Optional[int]:
    """Return non-negative webcam index for int, digit string, 'webcam', or 'webcam:<idx>'."""
    if isinstance(source, bool):
        return None
    if isinstance(source, int):
        return source if source >= 0 else None
    if not isinstance(source, str):
        return None
    text = source.strip().lower()
    if text == "webcam":
        return 0
    if text.startswith("webcam:"):
        raw_idx = text.split(":", 1)[1].strip()
        return int(raw_idx) if raw_idx.isdigit() else None
    if text.isdigit():
        return int(text)
    return None


def default_capture_factory(source: Any, use_rtsp: bool) -> Any:
    """Create an OpenCV VideoCapture for RTSP (TCP), DirectShow webcam, or video file."""
    import cv2

    webcam_index = None if use_rtsp else parse_webcam_source(source)
    if use_rtsp:
        ensure_rtsp_tcp_transport()
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    elif webcam_index is not None:
        dshow = getattr(cv2, "CAP_DSHOW", None)
        cap = (
            cv2.VideoCapture(webcam_index, dshow)
            if dshow is not None
            else cv2.VideoCapture(webcam_index)
        )
        try:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        except Exception:
            pass
    else:
        cap = cv2.VideoCapture(source)

    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


class RTSPCamera:
    """Threaded camera reader keeping only the newest frame with reconnect & watchdog."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        stream: str = "sub",
        source: Optional[str | Path | int] = None,
        *,
        ip: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        main_stream: Optional[str] = None,
        sub_stream: Optional[str] = None,
        on_event: Optional[EventCallback] = None,
        reconnect_initial_delay: Optional[float] = None,
        reconnect_max_delay: Optional[float] = None,
        stale_timeout: Optional[float] = None,
        loop_video: bool = True,
        capture_factory: Optional[CaptureFactory] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        cfg = settings or get_settings()

        self._ip = cfg.camera_ip if ip is None else ip
        self._port = cfg.camera_port if port is None else port
        self._username = cfg.camera_user if username is None else username
        self._password = cfg.camera_pass if password is None else password
        self._main_stream = cfg.camera_main_stream if main_stream is None else main_stream
        self._sub_stream = cfg.camera_sub_stream if sub_stream is None else sub_stream

        normalized_stream = stream.strip().lower()
        if normalized_stream not in ("main", "sub"):
            raise ValueError(f"Unsupported stream '{stream}'. Expected 'main' or 'sub'.")
        self._stream = normalized_stream

        self._on_event = on_event
        self._reconnect_initial_delay = max(
            0.01,
            cfg.rtsp_reconnect_initial_delay
            if reconnect_initial_delay is None
            else reconnect_initial_delay,
        )
        self._reconnect_max_delay = max(
            self._reconnect_initial_delay,
            cfg.rtsp_reconnect_max_delay
            if reconnect_max_delay is None
            else reconnect_max_delay,
        )
        self._stale_timeout = max(
            0.05,
            cfg.rtsp_stale_timeout if stale_timeout is None else stale_timeout,
        )
        self._loop_video = loop_video
        self._capture_factory = capture_factory or default_capture_factory
        self._monotonic = monotonic_fn

        self._secrets: tuple[str, ...] = tuple(
            s for s in (*cfg.secrets(), self._password) if s
        )

        # Build RTSP URLs when CAMERA_IP is configured.
        self._main_url = (
            build_rtsp_url(
                self._ip,
                self._username,
                self._password,
                stream=self._main_stream,
                port=self._port,
            )
            if self._ip
            else ""
        )
        self._sub_url = (
            build_rtsp_url(
                self._ip,
                self._username,
                self._password,
                stream=self._sub_stream,
                port=self._port,
            )
            if self._ip
            else ""
        )

        # Resolve active source: explicit arg -> CAMERA_SOURCE -> RTSP URL.
        if source is not None and str(source).strip() != "":
            self._active_source: Any = str(source) if isinstance(source, Path) else source
        elif cfg.camera_source:
            self._active_source = cfg.camera_source
        else:
            selected_url = self._sub_url if self._stream == "sub" else self._main_url
            if not selected_url:
                raise ValueError(
                    "No camera source configured. Set CAMERA_IP or CAMERA_SOURCE in .env, "
                    "or pass source explicitly."
                )
            self._active_source = selected_url

        self._is_rtsp = is_rtsp_source(self._active_source)
        self._is_webcam = (not self._is_rtsp) and (parse_webcam_source(self._active_source) is not None)
        self._is_file_source = (
            isinstance(self._active_source, str)
            and not self._is_rtsp
            and not self._is_webcam
        )

        self._frame_lock = threading.Lock()
        self._cap_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._cap: Any = None
        self._latest_frame: Any = None
        self._latest_frame_time: float = 0.0
        self._frame_seq: int = 0
        self._fps: float = 0.0
        self._fps_window_start: float = 0.0
        self._fps_window_frames: int = 0

        self._connected: bool = False
        self._last_frame_time: float = 0.0
        self._attempt: int = 0
        self._reconnect_requested = threading.Event()
        self._stop_event = threading.Event()

        self._reader_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None

    @property
    def main_url(self) -> str:
        return self._main_url

    @property
    def sub_url(self) -> str:
        return self._sub_url

    @property
    def active_source(self) -> Any:
        return self._active_source

    @property
    def masked_source(self) -> str:
        return mask_sensitive_text(str(self._active_source), self._secrets)

    @property
    def is_rtsp(self) -> bool:
        return self._is_rtsp

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    @property
    def fps(self) -> float:
        with self._frame_lock:
            return self._fps

    @property
    def frame_count(self) -> int:
        with self._frame_lock:
            return self._frame_seq

    def start(self) -> "RTSPCamera":
        """Start background reader and stale-frame watchdog threads."""
        if self._reader_thread and self._reader_thread.is_alive():
            return self

        self._stop_event.clear()
        self._reconnect_requested.clear()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"rtsp-reader-{self._stream}",
            daemon=True,
        )
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name=f"rtsp-watchdog-{self._stream}",
            daemon=True,
        )
        self._reader_thread.start()
        self._watchdog_thread.start()
        logger.info("Started RTSPCamera (%s): %s", self._stream, self.masked_source)
        return self

    def stop(self, timeout: float = 3.0) -> None:
        """Stop threads and release the active capture cleanly."""
        self._stop_event.set()
        self._reconnect_requested.set()
        self._release_capture()

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=timeout)
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=timeout)

        self._set_disconnected(reason="stopped", attempt=self._attempt)
        logger.info("Stopped RTSPCamera (%s): %s", self._stream, self.masked_source)

    def read(self, copy: bool = True) -> tuple[bool, Any]:
        """Return (True, newest_frame) if available, otherwise (False, None)."""
        with self._frame_lock:
            if self._latest_frame is None:
                return False, None
            frame = (
                self._latest_frame.copy()
                if copy and hasattr(self._latest_frame, "copy")
                else self._latest_frame
            )
            return True, frame

    def read_with_seq(self, copy: bool = True) -> tuple[bool, Any, int]:
        """Return (ok, newest_frame, sequence_number) for frame deduplication."""
        with self._frame_lock:
            if self._latest_frame is None:
                return False, None, self._frame_seq
            frame = (
                self._latest_frame.copy()
                if copy and hasattr(self._latest_frame, "copy")
                else self._latest_frame
            )
            return True, frame, self._frame_seq

    def __enter__(self) -> "RTSPCamera":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    def _emit_event(self, event: str, reason: str, attempt: int) -> None:
        if self._on_event is None:
            return
        payload = {
            "event": event,
            "source": self.masked_source,
            "stream": self._stream,
            "reason": mask_sensitive_text(reason, self._secrets),
            "attempt": attempt,
            "timestamp": time.time(),
        }
        try:
            self._on_event(event, payload)
        except Exception:
            logger.exception("Error inside camera event callback for '%s'", event)

    def _set_connected(self, attempt: int) -> None:
        changed = False
        with self._state_lock:
            self._last_frame_time = self._monotonic()
            if not self._connected:
                self._connected = True
                changed = True
        if changed:
            logger.info("Camera connected: %s", self.masked_source)
            self._emit_event("connected", reason="stream_opened", attempt=attempt)

    def _set_disconnected(self, reason: str, attempt: int) -> None:
        changed = False
        with self._state_lock:
            if self._connected:
                self._connected = False
                changed = True
        if changed:
            logger.warning(
                "Camera disconnected (%s): %s",
                mask_sensitive_text(reason, self._secrets),
                self.masked_source,
            )
            self._emit_event("disconnected", reason=reason, attempt=attempt)

    def _release_capture(self) -> None:
        with self._cap_lock:
            cap = self._cap
            self._cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                logger.debug("Ignoring error during VideoCapture.release()", exc_info=True)

    def _open_capture(self) -> Any:
        try:
            cap = self._capture_factory(self._active_source, self._is_rtsp)
        except Exception as exc:
            logger.warning(
                "Failed to open camera source %s: %s",
                self.masked_source,
                mask_sensitive_text(str(exc), self._secrets),
            )
            return None

        if cap is None or not cap.isOpened():
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            return None

        with self._cap_lock:
            if self._stop_event.is_set():
                try:
                    cap.release()
                except Exception:
                    pass
                return None
            self._cap = cap
        return cap

    def _next_backoff_delay(self) -> float:
        delay = min(
            self._reconnect_initial_delay * (2 ** self._attempt),
            self._reconnect_max_delay,
        )
        self._attempt += 1
        return delay

    def _update_latest_frame(self, frame: Any) -> None:
        now = self._monotonic()
        with self._state_lock:
            self._last_frame_time = now

        with self._frame_lock:
            self._latest_frame = frame
            self._latest_frame_time = now
            self._frame_seq += 1

            if self._fps_window_start == 0.0:
                self._fps_window_start = now
                self._fps_window_frames = 1
            else:
                self._fps_window_frames += 1
                elapsed = now - self._fps_window_start
                if elapsed >= 0.5:
                    self._fps = self._fps_window_frames / elapsed
                    self._fps_window_start = now
                    self._fps_window_frames = 0

    def _pace_file_playback(self, cap: Any, frame_start: float) -> None:
        if not self._is_file_source:
            return
        try:
            source_fps = float(cap.get(5))  # cv2.CAP_PROP_FPS == 5
        except Exception:
            source_fps = 0.0
        if source_fps <= 1.0 or source_fps > 120.0:
            source_fps = 30.0
        target_interval = 1.0 / source_fps
        elapsed = self._monotonic() - frame_start
        remaining = target_interval - elapsed
        if remaining > 0:
            self._stop_event.wait(remaining)

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            self._reconnect_requested.clear()
            cap = self._open_capture()
            if cap is None:
                self._set_disconnected(reason="open_failed", attempt=self._attempt)
                delay = self._next_backoff_delay()
                logger.info(
                    "Reconnecting to %s in %.2fs (attempt %d)",
                    self.masked_source,
                    delay,
                    self._attempt,
                )
                self._stop_event.wait(delay)
                continue

            self._set_connected(attempt=self._attempt)
            got_first_frame = False

            while not self._stop_event.is_set() and not self._reconnect_requested.is_set():
                frame_start = self._monotonic()
                try:
                    ok, frame = cap.read()
                except Exception as exc:
                    logger.warning(
                        "Capture read error on %s: %s",
                        self.masked_source,
                        mask_sensitive_text(str(exc), self._secrets),
                    )
                    ok, frame = False, None

                if self._stop_event.is_set() or self._reconnect_requested.is_set():
                    break

                if not ok or frame is None:
                    if self._is_file_source and self._loop_video:
                        try:
                            cap.set(1, 0)  # cv2.CAP_PROP_POS_FRAMES == 1
                            ok, frame = cap.read()
                        except Exception:
                            ok, frame = False, None

                    if not ok or frame is None:
                        self._set_disconnected(
                            reason="stream_read_failed",
                            attempt=self._attempt,
                        )
                        break

                if not got_first_frame:
                    got_first_frame = True
                    self._attempt = 0

                self._update_latest_frame(frame)
                self._pace_file_playback(cap, frame_start)

            self._release_capture()
            if self._stop_event.is_set():
                break

            delay = self._next_backoff_delay()
            logger.info(
                "Reconnecting to %s in %.2fs (attempt %d)",
                self.masked_source,
                delay,
                self._attempt,
            )
            self._stop_event.wait(delay)

    def _watchdog_loop(self) -> None:
        poll_interval = min(0.25, max(0.02, self._stale_timeout / 4.0))
        while not self._stop_event.wait(poll_interval):
            with self._state_lock:
                connected = self._connected
                last_frame = self._last_frame_time

            if not connected:
                continue

            elapsed = self._monotonic() - last_frame
            if elapsed > self._stale_timeout:
                logger.warning(
                    "Stale-frame watchdog triggered after %.2fs without frames: %s",
                    elapsed,
                    self.masked_source,
                )
                self._set_disconnected(
                    reason="stale_frame_timeout",
                    attempt=self._attempt,
                )
                self._reconnect_requested.set()
                self._release_capture()
