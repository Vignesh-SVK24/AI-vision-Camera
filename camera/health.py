"""Camera health monitoring, watchdog for stale frames, and jittered exponential backoff."""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable, Optional

from camera.base import CameraSource, CameraStatus

logger = logging.getLogger(__name__)


class ReconnectBackoff:
    """Calculates exponential backoff delay with randomized jitter."""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        factor: float = 2.0,
        jitter_range: tuple[float, float] = (0.1, 0.8),
    ) -> None:
        self.base_delay = max(0.1, float(base_delay))
        self.max_delay = max(self.base_delay, float(max_delay))
        self.factor = max(1.1, float(factor))
        self.jitter_range = jitter_range

    def get_delay(self, attempt: int) -> float:
        """Return computed delay in seconds for the given attempt number."""
        exp_delay = self.base_delay * (self.factor ** max(0, attempt))
        capped = min(self.max_delay, exp_delay)
        jitter = random.uniform(self.jitter_range[0], self.jitter_range[1])
        return round(capped + jitter, 2)


class CameraHealthWatchdog:
    """Monitors frame acquisition freshness and coordinates graceful recovery when streams stall."""

    def __init__(
        self,
        source: CameraSource,
        stale_timeout: float = 8.0,
        check_interval: float = 1.0,
        on_stale: Optional[Callable[[CameraSource], None]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> None:
        self._source = source
        self._stale_timeout = max(0.1, float(stale_timeout))
        self._check_interval = max(0.05, min(float(check_interval), self._stale_timeout / 2.0))
        self._on_stale = on_stale
        self._on_event = on_event

        self._last_frame_time: float = time.monotonic()
        self._last_seq: int = -1
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Wire listener to source status changes to emit events
        self._source.on_status_change(self._handle_status_change)

    def mark_frame_received(self, seq: int) -> None:
        """Update heartbeat timestamp upon receiving a new frame."""
        with self._lock:
            self._last_frame_time = time.monotonic()
            self._last_seq = seq

    def _handle_status_change(self, new_status: CameraStatus, metadata: dict[str, Any]) -> None:
        """Dispatch camera_online / camera_offline events based on source state transitions."""
        if not self._on_event:
            return

        source_label = self._source.masked_source
        if new_status == CameraStatus.CONNECTED:
            payload = {
                "event": "camera_online",
                "source": source_label,
                "status": new_status.value,
                "timestamp": time.time(),
                "metadata": metadata,
            }
            logger.info("Camera connected / online event emitted: %s", source_label)
            try:
                self._on_event("camera_online", payload)
            except Exception as exc:
                logger.warning("Error dispatching connected event: %s", exc)
        elif new_status in (CameraStatus.DISCONNECTED, CameraStatus.DEGRADED):
            reason = metadata.get("reason", "disconnected")
            payload = {
                "event": "camera_offline",
                "source": source_label,
                "status": new_status.value,
                "reason": reason,
                "timestamp": time.time(),
                "metadata": metadata,
            }
            logger.warning("Camera disconnected/offline event emitted (%s): %s", reason, source_label)
            try:
                self._on_event("camera_offline", payload)
            except Exception as exc:
                logger.warning("Error dispatching disconnected event: %s", exc)

    def start(self) -> None:
        """Start watchdog thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._running.set()
            self._last_frame_time = time.monotonic()
            self._thread = threading.Thread(
                target=self._watchdog_loop,
                name=f"CameraWatchdog-{self._source.masked_source}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop watchdog thread."""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _watchdog_loop(self) -> None:
        """Periodically check if frame delivery has stalled."""
        while self._running.is_set():
            time.sleep(self._check_interval)
            if not self._running.is_set():
                break

            now = time.monotonic()
            with self._lock:
                last_time = self._last_frame_time
                is_connected = self._source.is_connected

            if is_connected and (now - last_time > self._stale_timeout):
                logger.warning(
                    "Watchdog alert: No new frames received for %.1fs from %s. Marking degraded.",
                    now - last_time,
                    self._source.masked_source,
                )
                self._source._set_status(
                    CameraStatus.DEGRADED,
                    {"reason": "stale_frame_timeout", "stalled_seconds": round(now - last_time, 1)},
                )
                if self._on_stale:
                    try:
                        self._on_stale(self._source)
                    except Exception as exc:
                        logger.error("Error running on_stale callback: %s", exc)
