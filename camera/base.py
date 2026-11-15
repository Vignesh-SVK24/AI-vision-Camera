"""Abstract camera source interface, status definitions, frame packet, and credential masking."""

from __future__ import annotations

import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np


class CameraStatus(str, Enum):
    """Lifecycle connection status for camera sources."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class FramePacket:
    """Carries the latest captured frame in both full and downscaled (AI) resolutions."""

    full_frame: np.ndarray
    downscaled_frame: np.ndarray
    timestamp: float
    seq: int
    fps: float = 0.0


def mask_url_credentials(url: str) -> str:
    """Mask username and password in RTSP / HTTP URLs for secure logging."""
    if not url or "://" not in url:
        return url
    try:
        parsed = urlparse(url)
        if parsed.password is not None:
            netloc = parsed.netloc
            user = parsed.username or ""
            host_port = netloc.split("@")[-1]
            masked_netloc = f"{user}:***@{host_port}"
            return urlunparse(parsed._replace(netloc=masked_netloc))
        return url
    except Exception:
        return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", url)


def mask_sensitive_text(text: str, secrets: Optional[list[str]] = None) -> str:
    """Scrub known secret values and password fragments from logs and error strings."""
    if not text:
        return text
    masked = text
    if secrets:
        for secret in secrets:
            if secret and len(secret) >= 3 and secret in masked:
                masked = masked.replace(secret, "***")
    masked = re.sub(r"(password|token|secret|key)=([^&\s]+)", r"\1=***", masked, flags=re.IGNORECASE)
    masked = re.sub(r"://([^:@\s]+):([^@\s]+)@", r"://\1:***@", masked)
    return masked


def downscale_frame(frame: np.ndarray, target_width: int = 640) -> np.ndarray:
    """Downscale an image to target_width while preserving aspect ratio for AI inference."""
    if frame is None or frame.size == 0 or target_width <= 0:
        return frame
    height, width = frame.shape[:2]
    if width <= target_width:
        return frame
    scale = float(target_width) / float(width)
    new_height = max(1, int(round(height * scale)))
    return cv2.resize(frame, (target_width, new_height), interpolation=cv2.INTER_AREA)


class CameraSource(ABC):
    """Abstract base class defining the unified interface for all camera sources."""

    def __init__(
        self,
        *,
        downscale_width: int = 640,
        stale_timeout: float = 5.0,
    ) -> None:
        self._downscale_width = downscale_width
        self._stale_timeout = stale_timeout
        self._status: CameraStatus = CameraStatus.DISCONNECTED
        self._status_listeners: list[Callable[[CameraStatus, dict[str, Any]], None]] = []
        self._status_lock = threading.Lock()

        self._frame_lock = threading.Lock()
        self._latest_packet: Optional[FramePacket] = None
        self._frame_seq: int = 0
        self._fps: float = 0.0

    @property
    def status(self) -> CameraStatus:
        with self._status_lock:
            return self._status

    @property
    def is_connected(self) -> bool:
        with self._status_lock:
            return self._status == CameraStatus.CONNECTED

    @property
    def fps(self) -> float:
        with self._frame_lock:
            return self._fps

    @property
    def frame_count(self) -> int:
        with self._frame_lock:
            return self._frame_seq

    @property
    @abstractmethod
    def masked_source(self) -> str:
        """Return the credential-sanitized source descriptor."""
        ...

    def on_status_change(self, callback: Callable[[CameraStatus, dict[str, Any]], None]) -> None:
        """Register a callback invoked whenever the camera connection status changes."""
        with self._status_lock:
            if callback not in self._status_listeners:
                self._status_listeners.append(callback)

    def _set_status(self, new_status: CameraStatus, metadata: Optional[dict[str, Any]] = None) -> None:
        """Transition status and notify listeners thread-safely."""
        listeners: list[Callable[[CameraStatus, dict[str, Any]], None]] = []
        meta = metadata or {}
        with self._status_lock:
            if self._status == new_status:
                return
            old_status = self._status
            self._status = new_status
            meta["previous_status"] = old_status.value
            meta["status"] = new_status.value
            meta["source"] = self.masked_source
            listeners = list(self._status_listeners)

        for listener in listeners:
            try:
                listener(new_status, meta)
            except Exception:
                pass

    def _update_frame(self, frame: np.ndarray, calculated_fps: float = 0.0) -> FramePacket:
        """Store the newly captured frame, generating a downscaled copy for AI with zero buffer lag."""
        now = time.monotonic()
        downscaled = downscale_frame(frame, target_width=self._downscale_width)
        with self._frame_lock:
            self._frame_seq += 1
            if calculated_fps > 0:
                self._fps = calculated_fps
            packet = FramePacket(
                full_frame=frame,
                downscaled_frame=downscaled,
                timestamp=now,
                seq=self._frame_seq,
                fps=self._fps,
            )
            self._latest_packet = packet
            return packet

    def get_latest_frame(self, copy: bool = True) -> tuple[bool, Optional[FramePacket]]:
        """Return (True, FramePacket) if a frame is available, else (False, None)."""
        with self._frame_lock:
            if self._latest_packet is None:
                return False, None
            if not copy:
                return True, self._latest_packet
            packet = FramePacket(
                full_frame=self._latest_packet.full_frame.copy(),
                downscaled_frame=self._latest_packet.downscaled_frame.copy(),
                timestamp=self._latest_packet.timestamp,
                seq=self._latest_packet.seq,
                fps=self._latest_packet.fps,
            )
            return True, packet

    def read(self, copy: bool = True) -> tuple[bool, Optional[np.ndarray]]:
        """Convenience method returning (True, full_frame) for backward compatibility."""
        ok, packet = self.get_latest_frame(copy=copy)
        if not ok or packet is None:
            return False, None
        return True, packet.full_frame

    def read_with_seq(self, copy: bool = True) -> tuple[bool, Optional[np.ndarray], int]:
        """Convenience method returning (True, full_frame, seq) for deduplication."""
        with self._frame_lock:
            if self._latest_packet is None:
                return False, None, self._frame_seq
            frame = self._latest_packet.full_frame.copy() if copy else self._latest_packet.full_frame
            return True, frame, self._latest_packet.seq

    def get_ai_frame(self, copy: bool = True) -> tuple[bool, Optional[np.ndarray]]:
        """Return the downscaled frame optimized for YOLO / person detection."""
        ok, packet = self.get_latest_frame(copy=copy)
        if not ok or packet is None:
            return False, None
        return True, packet.downscaled_frame

    @abstractmethod
    def start(self) -> CameraSource:
        """Start the acquisition reader thread."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the acquisition thread and release hardware resources."""
        ...

    def __enter__(self) -> CameraSource:
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()
