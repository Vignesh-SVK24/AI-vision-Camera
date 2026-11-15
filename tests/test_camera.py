"""Unit tests for the unified camera package: factory selection, watchdog, reconnect, and security."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pytest

from camera.base import (
    CameraSource,
    CameraStatus,
    FramePacket,
    downscale_frame,
    mask_sensitive_text,
    mask_url_credentials,
)
from camera.factory import build_rtsp_url, create_camera_source
from camera.file_source import FileSource, generate_synthetic_test_clip
from camera.health import CameraHealthWatchdog, ReconnectBackoff
from camera.url_stream import URLStreamSource
from camera.webcam import WebcamSource, parse_webcam_source
from config.settings import Settings


class FakeCapture:
    """Thread-safe mock VideoCapture for unit tests."""

    def __init__(
        self,
        *,
        opened: bool = True,
        frames: Optional[list[np.ndarray]] = None,
        fail_after: Optional[int] = None,
    ) -> None:
        self._opened = opened
        self._frames = frames if frames is not None else [np.zeros((480, 640, 3), dtype=np.uint8)]
        self._fail_after = fail_after
        self._count = 0
        self._released = False

    def isOpened(self) -> bool:
        return self._opened and not self._released

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        if not self._opened or self._released:
            return False, None
        if self._fail_after is not None and self._count >= self._fail_after:
            return False, None
        frame = self._frames[self._count % len(self._frames)]
        self._count += 1
        return True, frame.copy()

    def set(self, prop: int, value: float) -> bool:
        return True

    def get(self, prop: int) -> float:
        return 30.0

    def release(self) -> None:
        self._released = True


def test_credential_masking() -> None:
    raw_rtsp = "rtsp://camera_admin:SuperSecretPass123@192.168.1.105:554/stream1"
    masked = mask_url_credentials(raw_rtsp)
    assert "SuperSecretPass123" not in masked
    assert "camera_admin:***@192.168.1.105:554" in masked

    text = "Failed connecting to rtsp://alice:mypassword@10.0.0.1:554 with token=xyz789"
    scrubbed = mask_sensitive_text(text, secrets=["xyz789", "mypassword"])
    assert "mypassword" not in scrubbed
    assert "xyz789" not in scrubbed
    assert "***" in scrubbed


def test_factory_webcam_selection() -> None:
    settings = Settings(
        camera_source="webcam",
        webcam_index=2,
        webcam_backend="msmf",
    )
    source = create_camera_source(settings)
    assert isinstance(source, WebcamSource)
    assert source.index == 2
    assert source.backend == "msmf"
    assert "webcam:2 (msmf)" in source.masked_source


def test_factory_url_selection() -> None:
    url = "rtsp://viewer:pass123@192.168.1.55:554/live"
    settings = Settings(camera_source=url)
    source = create_camera_source(settings)
    assert isinstance(source, URLStreamSource)
    assert "pass123" not in source.masked_source
    assert "viewer:***@192.168.1.55:554/live" in source.masked_source


def test_factory_file_selection(tmp_path: Path) -> None:
    sample_file = tmp_path / "test_video.mp4"
    settings = Settings(camera_source=str(sample_file))
    source = create_camera_source(settings)
    assert isinstance(source, FileSource)
    assert "test_video.mp4" in source.masked_source


def test_reconnect_backoff_exponential_with_jitter() -> None:
    backoff = ReconnectBackoff(base_delay=1.0, max_delay=10.0, factor=2.0)
    delay_0 = backoff.get_delay(0)
    delay_1 = backoff.get_delay(1)
    delay_2 = backoff.get_delay(2)
    delay_10 = backoff.get_delay(10)

    # Base delay ~ 1.0 + jitter
    assert 1.0 <= delay_0 <= 2.0
    # Attempt 1 ~ 2.0 + jitter
    assert 2.0 <= delay_1 <= 3.0
    # Attempt 2 ~ 4.0 + jitter
    assert 4.0 <= delay_2 <= 5.0
    # Capped at max_delay 10.0 + jitter
    assert 10.0 <= delay_10 <= 11.0


def test_frame_packet_and_downscale() -> None:
    full = np.zeros((1080, 1920, 3), dtype=np.uint8)
    downscaled = downscale_frame(full, target_width=640)
    assert downscaled.shape[1] == 640
    assert downscaled.shape[0] == 360

    source = WebcamSource(
        index=0,
        capture_factory=lambda *args: FakeCapture(frames=[full]),
        warmup_seconds=1.0,
    )
    with source:
        # Allow thread to capture
        time.sleep(0.15)
        ok, packet = source.get_latest_frame()
        assert ok is True
        assert packet is not None
        assert packet.full_frame.shape == (1080, 1920, 3)
        assert packet.downscaled_frame.shape == (360, 640, 3)

        ai_ok, ai_frame = source.get_ai_frame()
        assert ai_ok is True
        assert ai_frame.shape == (360, 640, 3)


def test_watchdog_detects_stale_stream() -> None:
    events_recorded: list[tuple[str, dict[str, Any]]] = []

    def on_event(name: str, payload: dict[str, Any]) -> None:
        events_recorded.append((name, payload))

    source = WebcamSource(
        index=0,
        stale_timeout=0.25,
        capture_factory=lambda *args: FakeCapture(frames=[np.zeros((10, 10, 3), dtype=np.uint8)]),
        on_event=on_event,
    )
    source._set_status(CameraStatus.CONNECTED)
    source._watchdog.mark_frame_received(1)

    source._watchdog.start()
    try:
        assert source.status == CameraStatus.CONNECTED
        # Wait longer than stale_timeout (0.25s) without sending new frames
        time.sleep(0.45)
        assert source.status in (CameraStatus.DEGRADED, CameraStatus.DISCONNECTED)
        assert any(e[0] in ("camera_offline", "disconnected") for e in events_recorded)
    finally:
        source._watchdog.stop()


def test_synthetic_test_clip_generation(tmp_path: Path) -> None:
    target_path = tmp_path / "sample_clip.mp4"
    generated = generate_synthetic_test_clip(target_path, width=320, height=240, duration_seconds=1, fps=10)
    assert generated.is_file()
    assert generated.stat().st_size > 100
