"""Tests for live web webcam/camera streaming endpoints and manager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import create_owner_account
from backend.live_stream import LiveCameraManager
from camera.rtsp_client import RTSPCamera, default_capture_factory, parse_webcam_source
from config.settings import Settings
from storage.db import Database


class _FakeCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._opened = True

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        if not self._opened:
            return False, None
        return True, self._frame.copy()

    def release(self) -> None:
        self._opened = False


class _FakeSharedCamera:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame
        self.started_calls = 0
        self.stopped_calls = 0
        self.is_connected = False
        self.fps = 24.5
        self._seq = 0

    def start(self) -> None:
        self.started_calls += 1
        self.is_connected = True
        self._seq += 1

    def stop(self) -> None:
        self.stopped_calls += 1
        self.is_connected = False

    def read_with_seq(self, copy: bool = False):
        if not self.is_connected:
            return False, None, 0
        out = self._frame.copy() if copy else self._frame
        return True, out, self._seq


def _make_sample_frame() -> np.ndarray:
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    img[:, :] = (32, 64, 48)
    cv2.rectangle(img, (100, 100), (400, 360), (220, 240, 230), 3)
    return img


def _build_live_test_context(tmp_path: Path):
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "live.db",
        snapshots_dir=tmp_path / "snapshots",
        faces_data_dir=tmp_path / "faces_data",
        camera_name="Laptop Webcam",
        camera_source="webcam:0",
        jwt_secret_key="test-only-random-jwt-signing-key-32bytes!",
    )
    db = Database(db_path=settings.db_path, settings=settings)
    create_owner_account(db, username="owner", password="StrongPassword123!")

    frame = _make_sample_frame()
    fake_cameras: list[_FakeSharedCamera] = []

    def _factory(_settings: Settings):
        cam = _FakeSharedCamera(frame)
        fake_cameras.append(cam)
        return cam

    live_manager = LiveCameraManager(settings=settings, camera_factory=_factory)
    app = create_app(
        settings=settings,
        db=db,
        zones_path=tmp_path / "zones.json",
        ws_poll_interval=0.05,
        live_manager=live_manager,
    )
    return settings, db, app, live_manager, fake_cameras


def test_parse_webcam_source_variants() -> None:
    assert parse_webcam_source(None) is None
    assert parse_webcam_source("") is None
    assert parse_webcam_source("webcam") == 0
    assert parse_webcam_source("WEBCAM:1") == 1
    assert parse_webcam_source("0") == 0
    assert parse_webcam_source(2) == 2
    assert parse_webcam_source("rtsp://user:pass@192.168.1.10/stream2") is None
    assert parse_webcam_source("clips/sample.mp4") is None


def test_default_capture_factory_configures_dshow_webcam(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, Any] = {"sets": []}

    class _DummyCap:
        def set(self, prop: int, value: float) -> bool:
            recorded["sets"].append((prop, value))
            return True

    def _fake_video_capture(source: Any, backend: Any = None):
        recorded["source"] = source
        recorded["backend"] = backend
        return _DummyCap()

    monkeypatch.setattr(cv2, "VideoCapture", _fake_video_capture)
    cap = default_capture_factory("webcam:0")
    assert isinstance(cap, _DummyCap)
    assert recorded["source"] == 0
    assert recorded["backend"] == getattr(cv2, "CAP_DSHOW", None)
    assert (cv2.CAP_PROP_FRAME_WIDTH, 1280) in recorded["sets"]
    assert (cv2.CAP_PROP_FRAME_HEIGHT, 720) in recorded["sets"]
    assert (cv2.CAP_PROP_BUFFERSIZE, 1) in recorded["sets"]

    cam = RTSPCamera(
        source="webcam:0",
        capture_factory=lambda _src: _FakeCapture(_make_sample_frame()),
    )
    assert cam._is_webcam is True
    assert cam._is_file_source is False


def test_live_camera_manager_refcounts_shared_camera(tmp_path: Path) -> None:
    settings = Settings(log_dir=tmp_path / "logs", camera_source="webcam:0")
    frame = _make_sample_frame()
    created: list[_FakeSharedCamera] = []

    def _factory(_settings: Settings):
        cam = _FakeSharedCamera(frame)
        created.append(cam)
        return cam

    manager = LiveCameraManager(settings=settings, camera_factory=_factory)
    assert manager.viewer_count == 0
    assert manager.get_state()["status"] == "disconnected"

    manager.acquire()
    manager.acquire()
    assert manager.viewer_count == 2
    assert len(created) == 1
    assert created[0].started_calls == 1
    assert manager.get_state()["status"] == "connected"

    jpeg, seq = manager.get_latest_jpeg(last_seq=-1)
    assert jpeg is not None and jpeg.startswith(b"\xff\xd8")
    assert seq >= 1

    manager.release()
    assert manager.viewer_count == 1
    assert created[0].stopped_calls == 0

    manager.release()
    assert manager.viewer_count == 0
    assert created[0].stopped_calls == 1
    assert manager.get_state()["status"] == "disconnected"


@pytest.mark.anyio
async def test_live_snapshot_requires_auth_and_returns_jpeg(tmp_path: Path) -> None:
    _, db, app, live_manager, fake_cameras = _build_live_test_context(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            unauth = await client.get("/cameras/live/snapshot")
            assert unauth.status_code == 401

            login = await client.post(
                "/auth/login",
                json={"username": "owner", "password": "StrongPassword123!"},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]

            snap = await client.get(
                "/cameras/live/snapshot",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert snap.status_code == 200
            assert snap.headers["content-type"].startswith("image/jpeg")
            assert snap.headers["cache-control"] == "no-store"
            assert snap.content.startswith(b"\xff\xd8")
            assert live_manager.viewer_count == 0
            assert len(fake_cameras) == 1
            assert fake_cameras[0].stopped_calls == 1
    finally:
        live_manager.close()
        db.close()


def test_ws_live_requires_auth_and_streams_frames(tmp_path: Path) -> None:
    _, db, app, live_manager, fake_cameras = _build_live_test_context(tmp_path)
    try:
        with TestClient(app) as client:
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/live"):
                    pass

            login = client.post(
                "/auth/login",
                json={"username": "owner", "password": "StrongPassword123!"},
            )
            token = login.json()["access_token"]

            with client.websocket_connect(f"/ws/live?token={token}") as ws:
                status_msg = ws.receive_json()
                assert status_msg["type"] == "status"
                assert status_msg["status"] == "connected"
                assert status_msg["camera_name"] == "Laptop Webcam"

                frame_bytes = ws.receive_bytes()
                assert frame_bytes.startswith(b"\xff\xd8")

                status_resp = client.get(
                    "/cameras/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert status_resp.status_code == 200
                assert status_resp.json()["status"] == "online"

        assert live_manager.viewer_count == 0
        assert len(fake_cameras) == 1
        assert fake_cameras[0].stopped_calls == 1
    finally:
        live_manager.close()
        db.close()
