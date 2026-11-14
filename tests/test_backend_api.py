"""API tests using pytest, httpx.AsyncClient, and FastAPI WebSocket TestClient."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import create_owner_account
from config.settings import Settings
from events.event_manager import EventManager
from faces.face_engine import DetectedFace, FaceEngine, l2_normalize
from storage.db import Database


class _FakeAPIFaceEngine(FaceEngine):
    def __init__(self) -> None:
        super().__init__(yunet_model_path="unused.onnx", sface_model_path="unused.onnx")

    def detect_faces(self, image: np.ndarray) -> list[DetectedFace]:
        return [DetectedFace((20, 20, 100, 100), 0.99, np.zeros(15, dtype=np.float32))]

    def extract_embedding(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        return l2_normalize(np.array([1.0, 0.2, 0.0, 0.0], dtype=np.float32))


def _make_sharp_jpeg_bytes() -> bytes:
    img = np.zeros((240, 240, 3), dtype=np.uint8)
    for y in range(0, 240, 8):
        for x in range(0, 240, 8):
            if ((x // 8) + (y // 8)) % 2 == 0:
                img[y : y + 8, x : x + 8] = (255, 255, 255)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _build_test_context(tmp_path: Path, **setting_overrides):
    defaults = dict(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "api.db",
        snapshots_dir=tmp_path / "snapshots",
        faces_data_dir=tmp_path / "faces_data",
        camera_name="Front Door",
        camera_pass="TopSecretCam!",
        telegram_bot_token="999:TopSecretBot",
        jwt_secret_key="test-only-random-jwt-signing-key-32bytes!",
        login_rate_limit_max_attempts=3,
        login_rate_limit_window_seconds=60,
        upload_max_bytes=500_000,
        cors_allowed_origins=("http://localhost:5173",),
    )
    defaults.update(setting_overrides)
    settings = Settings(**defaults)
    db = Database(db_path=settings.db_path, settings=settings)
    create_owner_account(db, username="owner", password="StrongPassword123!")
    app = create_app(
        settings=settings,
        db=db,
        face_engine=_FakeAPIFaceEngine(),
        zones_path=tmp_path / "zones.json",
        ws_poll_interval=0.05,
    )
    return settings, db, app


@pytest.mark.anyio
async def test_health_openapi_and_unauthenticated_rejected(tmp_path: Path) -> None:
    _, db, app = _build_test_context(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            docs = await client.get("/docs")
            assert docs.status_code == 200

            openapi = await client.get("/openapi.json")
            assert openapi.status_code == 200
            assert "/events" in openapi.json()["paths"]
            assert "/auth/register" not in openapi.json()["paths"]

            protected_gets = [
                "/auth/me",
                "/cameras/status",
                "/events",
                "/snapshots/1",
                "/people",
                "/zones",
                "/settings",
                "/notifications",
            ]
            for path in protected_gets:
                resp = await client.get(path)
                assert resp.status_code == 401, f"{path} should reject unauthenticated access"
    finally:
        db.close()


@pytest.mark.anyio
async def test_auth_login_refresh_rate_limit_and_cors(tmp_path: Path) -> None:
    _, db, app = _build_test_context(tmp_path)
    try:
        user_row = db.get_user_by_username("owner")
        assert user_row is not None
        assert user_row["password_hash"].startswith("$argon2")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            # 3 failed attempts allowed, 4th triggers HTTP 429 rate limit
            for _ in range(3):
                bad = await client.post(
                    "/auth/login",
                    json={"username": "owner", "password": "wrong_password"},
                )
                assert bad.status_code == 401

            blocked = await client.post(
                "/auth/login",
                json={"username": "owner", "password": "StrongPassword123!"},
            )
            assert blocked.status_code == 429
            assert "Retry-After" in blocked.headers

        # Login with another username/ip still succeeds and returns access + refresh tokens
        create_owner_account(db, username="owner2", password="AnotherStrongPassword9!")
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            ok_login = await client.post(
                "/auth/login",
                json={"username": "owner2", "password": "AnotherStrongPassword9!"},
                headers={"Origin": "http://localhost:5173"},
            )
            assert ok_login.status_code == 200
            assert ok_login.headers.get("access-control-allow-origin") == "http://localhost:5173"

            tokens = ok_login.json()
            access = tokens["access_token"]
            refresh = tokens["refresh_token"]

            # Refresh token must NOT work as bearer access token
            wrong_type = await client.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {refresh}"},
            )
            assert wrong_type.status_code == 401

            # Refresh endpoint issues new tokens
            refreshed = await client.post("/auth/refresh", json={"refresh_token": refresh})
            assert refreshed.status_code == 200
            assert "access_token" in refreshed.json()

            # Untrusted CORS origin is not echoed
            bad_cors = await client.get(
                "/auth/me",
                headers={
                    "Authorization": f"Bearer {access}",
                    "Origin": "https://evil.example.com",
                },
            )
            assert bad_cors.status_code == 200
            assert "access-control-allow-origin" not in bad_cors.headers
    finally:
        db.close()


@pytest.mark.anyio
async def test_events_snapshots_people_upload_zones_settings_notifications(tmp_path: Path) -> None:
    settings, db, app = _build_test_context(tmp_path, upload_max_bytes=50_000)
    try:
        mgr = EventManager(db, settings=settings)
        frame = np.full((240, 320, 3), 150, dtype=np.uint8)
        mgr.record_camera_online()
        eid = mgr.record_person_detected(
            5,
            confidence=0.9,
            frame=frame,
            created_at=datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert eid is not None

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            login_res = await client.post(
                "/auth/login",
                json={"username": "owner", "password": "StrongPassword123!"},
            )
            token = login_res.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Camera status
            cam = await client.get("/cameras/status", headers=headers)
            assert cam.status_code == 200
            assert cam.json()["status"] == "online"

            # Events filtering
            ev_res = await client.get(
                "/events",
                params={"type": "person_detected", "start_date": "2026-11-12", "end_date": "2026-11-12"},
                headers=headers,
            )
            assert ev_res.status_code == 200
            assert ev_res.json()["count"] == 1
            assert ev_res.json()["events"][0]["snapshot_url"] == f"/snapshots/{eid}"

            # Authenticated snapshot retrieval
            snap = await client.get(f"/snapshots/{eid}", headers=headers)
            assert snap.status_code == 200
            assert snap.headers["content-type"].startswith("image/jpeg")

            # Upload security: reject fake magic bytes & oversized file
            bad_magic = await client.post(
                "/people",
                data={"name": "Alice", "consent_date": "2026-11-12", "consent_note": "Family"},
                files={"files": ("evil.jpg", b"NOT_A_REAL_JPEG_BYTES", "image/jpeg")},
                headers=headers,
            )
            assert bad_magic.status_code == 400

            too_big = await client.post(
                "/people",
                data={"name": "Alice", "consent_date": "2026-11-12", "consent_note": "Family"},
                files={"files": ("big.jpg", b"\xff\xd8\xff" + b"0" * 60_000, "image/jpeg")},
                headers=headers,
            )
            assert too_big.status_code == 413

            # Valid photo upload enrolls person, then list and delete
            jpeg_bytes = _make_sharp_jpeg_bytes()
            enroll = await client.post(
                "/people",
                data={"name": "Alice", "consent_date": "2026-11-12", "consent_note": "Family"},
                files={"files": ("alice.jpg", jpeg_bytes, "image/jpeg")},
                headers=headers,
            )
            assert enroll.status_code == 201
            alice_id = enroll.json()["person_id"]

            listed = await client.get("/people", headers=headers)
            assert len(listed.json()) == 1
            assert listed.json()[0]["name"] == "Alice"

            deleted = await client.delete(f"/people/{alice_id}", headers=headers)
            assert deleted.status_code == 200
            assert (await client.get("/people", headers=headers)).json() == []

            # Zones GET & PUT
            put_zones = await client.put(
                "/zones",
                json={
                    "zones": [
                        {
                            "id": "porch",
                            "name": "Porch",
                            "enabled": True,
                            "dwell_seconds": 1.0,
                            "points": [[0, 0], [100, 0], [100, 100]],
                            "color": [0, 165, 255],
                        }
                    ]
                },
                headers=headers,
            )
            assert put_zones.status_code == 200
            get_zones = await client.get("/zones", headers=headers)
            assert len(get_zones.json()["zones"]) == 1

            # Settings GET & PUT (no secrets leaked)
            get_cfg = await client.get("/settings", headers=headers)
            assert get_cfg.status_code == 200
            assert "TopSecretCam!" not in get_cfg.text
            assert "TopSecretBot" not in get_cfg.text

            put_cfg = await client.put(
                "/settings",
                json={"overrides": {"detection_confidence": "0.55"}},
                headers=headers,
            )
            assert put_cfg.status_code == 200
            assert put_cfg.json()["overrides"]["detection_confidence"] == "0.55"

            # Notifications outbox & audit log
            notif = await client.get("/notifications", headers=headers)
            assert notif.status_code == 200
            assert len(notif.json()) == 2

            audits = await client.get("/audit-logs", headers=headers)
            assert audits.status_code == 200
            actions = {a["action"] for a in audits.json()}
            assert {"login_success", "api_enroll_person", "api_delete_person", "api_update_zones", "api_update_settings"}.issubset(actions)
    finally:
        db.close()


def test_websocket_events_requires_auth_and_streams_live_updates(tmp_path: Path) -> None:
    _, db, app = _build_test_context(tmp_path)
    try:
        with TestClient(app) as client:
            # Unauthenticated WebSocket is rejected
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/events"):
                    pass

            login = client.post(
                "/auth/login",
                json={"username": "owner", "password": "StrongPassword123!"},
            )
            token = login.json()["access_token"]

            with client.websocket_connect(f"/ws/events?token={token}") as ws:
                hello = ws.receive_json()
                assert hello["type"] == "connected"

                # Simulate engine process writing a new event to shared SQLite DB
                db.insert_event(event_type="zone_intrusion", zone_id="z1", zone_name="Gate", track_id=9)
                msg = ws.receive_json()
                assert msg["type"] == "event"
                assert msg["data"]["event_type"] == "zone_intrusion"
                assert msg["data"]["zone_name"] == "Gate"
    finally:
        db.close()
