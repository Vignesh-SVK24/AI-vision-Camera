"""Tests for RTSPCamera URL building, TCP transport, recovery, watchdog, and logging."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from camera.rtsp_client import (
    RTSPCamera,
    build_rtsp_url,
    default_capture_factory,
    ensure_rtsp_tcp_transport,
)
from config.logging_config import setup_logging
from config.settings import Settings


def _wait_until(predicate, timeout: float = 3.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class FakeCapture:
    """Thread-safe fake VideoCapture supporting drops, open failures, and stalls."""

    def __init__(
        self,
        *,
        opened: bool = True,
        frames: list[np.ndarray] | None = None,
        fail_after: int | None = None,
        stall_after: int | None = None,
        frame_delay: float = 0.005,
    ) -> None:
        self._opened = opened
        self._frames = frames if frames is not None else []
        self._fail_after = fail_after
        self._stall_after = stall_after
        self._frame_delay = frame_delay
        self._index = 0
        self._released = threading.Event()

    def isOpened(self) -> bool:
        return self._opened and not self._released.is_set()

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._released.is_set() or not self._opened:
            return False, None

        if self._fail_after is not None and self._index >= self._fail_after:
            return False, None

        if self._stall_after is not None and self._index >= self._stall_after:
            # Block until watchdog/stop releases the capture.
            self._released.wait(timeout=5.0)
            return False, None

        time.sleep(self._frame_delay)
        if self._released.is_set():
            return False, None

        if self._frames:
            frame = self._frames[self._index % len(self._frames)]
        else:
            frame = np.full((8, 8, 3), self._index % 255, dtype=np.uint8)

        self._index += 1
        return True, frame

    def set(self, prop_id: int, value: float) -> bool:
        if prop_id == 1:  # cv2.CAP_PROP_POS_FRAMES
            self._index = int(value)
        return True

    def get(self, prop_id: int) -> float:
        if prop_id == 5:  # cv2.CAP_PROP_FPS
            return 30.0
        return 0.0

    def release(self) -> None:
        self._opened = False
        self._released.set()


def test_build_rtsp_urls_and_force_tcp(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)
    settings = Settings(
        log_dir=tmp_path / "logs",
        camera_ip="192.168.1.25",
        camera_port=554,
        camera_user="cam_user",
        camera_pass="P@ss:word!",
        camera_main_stream="stream1",
        camera_sub_stream="stream2",
    )

    cam_sub = RTSPCamera(
        settings=settings,
        stream="sub",
        capture_factory=lambda src, rtsp: FakeCapture(opened=False),
    )
    cam_main = RTSPCamera(
        settings=settings,
        stream="main",
        capture_factory=lambda src, rtsp: FakeCapture(opened=False),
    )

    assert cam_main.main_url == "rtsp://cam_user:P%40ss%3Aword%21@192.168.1.25:554/stream1"
    assert cam_sub.sub_url == "rtsp://cam_user:P%40ss%3Aword%21@192.168.1.25:554/stream2"
    assert cam_sub.active_source == cam_sub.sub_url
    assert cam_main.active_source == cam_main.main_url
    assert "P@ss:word!" not in cam_sub.masked_source
    assert "P%40ss%3Aword%21" not in cam_sub.masked_source
    assert cam_sub.is_rtsp is True

    opts = ensure_rtsp_tcp_transport()
    assert "rtsp_transport;tcp" in opts
    assert os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"].startswith("rtsp_transport;tcp")

    # Verify default_capture_factory sets TCP transport when use_rtsp=True
    monkeypatch.setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;udp|stimeout;1000")
    cap = default_capture_factory("rtsp://invalid.local/stream2", use_rtsp=True)
    cap.release()
    assert "rtsp_transport;tcp" in os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
    assert "rtsp_transport;udp" not in os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
    assert "stimeout;1000" in os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"]
    assert build_rtsp_url("10.0.0.1", stream="stream2") == "rtsp://10.0.0.1:554/stream2"


def test_newest_frame_only_no_lag_buildup(tmp_path: Path) -> None:
    settings = Settings(log_dir=tmp_path / "logs")
    frames = [np.full((4, 4, 3), i, dtype=np.uint8) for i in range(50)]
    cap = FakeCapture(frames=frames, frame_delay=0.001)

    cam = RTSPCamera(
        settings=settings,
        source="rtsp://127.0.0.1:554/stream2",
        capture_factory=lambda src, rtsp: cap,
    )
    with cam:
        assert _wait_until(lambda: cam.frame_count >= 15, timeout=2.0)
        ok, latest = cam.read()
        assert ok is True
        assert latest is not None
        # Newest frame should be well past frame 0 (not queued from the start).
        assert int(latest[0, 0, 0]) >= 5


def test_simulated_disconnect_and_exponential_backoff_recovery(tmp_path: Path) -> None:
    secret_pass = "VerySecretPass!42"
    settings = Settings(
        log_dir=tmp_path / "logs",
        camera_ip="192.168.1.99",
        camera_user="admin",
        camera_pass=secret_pass,
        rtsp_reconnect_initial_delay=0.05,
        rtsp_reconnect_max_delay=0.2,
        rtsp_stale_timeout=1.0,
    )
    setup_logging(settings)

    events: list[tuple[str, dict[str, Any]]] = []
    open_calls = 0

    def factory(src: Any, use_rtsp: bool) -> FakeCapture:
        nonlocal open_calls
        open_calls += 1
        if open_calls == 1:
            # First connection yields 3 frames then drops.
            return FakeCapture(fail_after=3, frame_delay=0.01)
        if open_calls == 2:
            # Second attempt fails to open (exercises exponential backoff).
            return FakeCapture(opened=False)
        # Third attempt recovers and streams normally.
        return FakeCapture(frame_delay=0.01)

    cam = RTSPCamera(
        settings=settings,
        stream="sub",
        on_event=lambda name, payload: events.append((name, payload)),
        capture_factory=factory,
    )

    with cam:
        recovered = _wait_until(
            lambda: [e[0] for e in events] == ["connected", "disconnected", "connected"]
            and cam.frame_count >= 5,
            timeout=4.0,
        )
        assert recovered, f"Unexpected events: {events}"

    # After exiting context manager, a final disconnected ('stopped') event is emitted.
    event_names = [e[0] for e in events]
    assert event_names[:3] == ["connected", "disconnected", "connected"]
    assert event_names[-1] == "disconnected"

    # Password must never appear in event payloads or log files.
    for _, payload in events:
        assert secret_pass not in str(payload)

    for handler in logging.getLogger().handlers:
        handler.flush()
    log_contents = settings.log_path.read_text(encoding="utf-8")
    assert secret_pass not in log_contents
    assert "VerySecretPass" not in log_contents


def test_stale_frame_watchdog_triggers_reconnect(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        rtsp_reconnect_initial_delay=0.05,
        rtsp_reconnect_max_delay=0.1,
        rtsp_stale_timeout=0.15,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    open_calls = 0

    def factory(src: Any, use_rtsp: bool) -> FakeCapture:
        nonlocal open_calls
        open_calls += 1
        if open_calls == 1:
            # Deliver 1 frame, then stall until watchdog releases capture.
            return FakeCapture(stall_after=1, frame_delay=0.01)
        return FakeCapture(frame_delay=0.01)

    cam = RTSPCamera(
        settings=settings,
        source="rtsp://127.0.0.1:554/stream2",
        on_event=lambda name, payload: events.append((name, payload)),
        capture_factory=factory,
    )

    with cam:
        ok = _wait_until(
            lambda: len(events) >= 3
            and events[1][0] == "disconnected"
            and events[1][1]["reason"] == "stale_frame_timeout"
            and events[2][0] == "connected",
            timeout=4.0,
        )
        assert ok, f"Watchdog did not recover as expected. Events: {events}"


def test_video_file_source_works_without_camera(tmp_path: Path) -> None:
    video_path = tmp_path / "sample.avi"
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(str(video_path), fourcc, 15.0, (64, 48))
    assert writer.isOpened()
    for idx in range(5):
        frame = np.full((48, 64, 3), idx * 40, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    settings = Settings(log_dir=tmp_path / "logs")
    events: list[str] = []
    cam = RTSPCamera(
        settings=settings,
        source=video_path,
        on_event=lambda name, _: events.append(name),
    )

    assert cam.is_rtsp is False
    with cam:
        assert _wait_until(lambda: cam.frame_count >= 3, timeout=3.0)
        ok, frame = cam.read()
        assert ok is True
        assert frame is not None
        assert frame.shape == (48, 64, 3)

    assert events[0] == "connected"
