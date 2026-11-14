"""Unit tests for Telegram alerts, rules, rate limiting, and outbox retry."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

from config.logging_config import setup_logging
from config.settings import Settings
from events.event_manager import EventManager
from notifications.telegram import (
    OutboxWorker,
    RateLimiter,
    TelegramClient,
    TelegramDeliveryError,
)
from storage.db import Database


class FakeTelegramServer:
    """Mock Telegram Bot API transport supporting simulated internet outages."""

    def __init__(self, *, online: bool = True) -> None:
        self.online = online
        self.requests: list[dict[str, Any]] = []

    def transport(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        if not self.online:
            raise TelegramDeliveryError(f"Connection timed out calling {url}")

        method = url.rsplit("/", 1)[-1]
        content_type = headers.get("Content-Type", "")
        text = ""
        has_photo = False
        disable_notification = None

        if "application/json" in content_type:
            parsed = json.loads(body.decode("utf-8"))
            text = parsed.get("text", "")
            disable_notification = parsed.get("disable_notification")
        else:
            decoded = body.decode("utf-8", errors="ignore")
            text = decoded
            has_photo = 'name="photo"' in decoded
            disable_notification = 'name="disable_notification"\r\n\r\ntrue' in decoded

        self.requests.append(
            {
                "method": method,
                "url": url,
                "text": text,
                "has_photo": has_photo,
                "disable_notification": disable_notification,
            }
        )
        return 200, json.dumps({"ok": True, "result": {"message_id": len(self.requests)}}).encode("utf-8")


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_telegram_rules_message_format_and_token_masking(tmp_path: Path) -> None:
    bot_token = "123456789:ABCDEF_SECRET_TOKEN_XYZ"
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "tg_rules.db",
        snapshots_dir=tmp_path / "snapshots",
        camera_name="Driveway Cam",
        telegram_bot_token=bot_token,
        telegram_chat_id="987654321",
        alert_on_unknown_person=True,
        alert_on_known_person=False,
        alert_on_zone_intrusion=True,
        alert_on_camera_offline=True,
        alert_on_camera_online=True,
        alert_on_system_error=True,
        telegram_min_interval_seconds=0.0,
    )
    setup_logging(settings)
    server = FakeTelegramServer(online=True)
    client = TelegramClient(settings=settings, transport=server.transport)
    frame = np.full((200, 320, 3), 180, dtype=np.uint8)

    with Database(db_path=settings.db_path, settings=settings) as db:
        mgr = EventManager(db, settings=settings)

        # 1. Known family member -> logged quietly, NOT sent to Telegram
        alice_id = db.create_person(
            name="Alice",
            consent_date="2026-11-12",
            consent_note="Family consent",
            is_trusted=True,
        )
        db.insert_event(
            event_type="person_detected",
            track_id=1,
            person_id=alice_id,
            message="Person detected (Alice)",
        )

        # 2. Unknown person with snapshot -> high-priority sendPhoto
        mgr.record_person_detected(2, confidence=0.9, frame=frame)

        # 3. Zone intrusion with snapshot -> high-priority sendPhoto
        mgr.record_zone_intrusion("gate", "Front Gate", 3, dwell_seconds=1.2, frame=frame)

        # 4. Camera offline, camera online, system error
        mgr.record_camera_offline("timeout")
        mgr.record_camera_online()
        mgr.record_system_error("Disk warning")

        worker = OutboxWorker(
            db,
            client=client,
            settings=settings,
            rate_limiter=RateLimiter(max_per_minute=60, min_interval_seconds=0.0),
        )
        batch = worker.process_once()

        assert batch.quietly_logged == 1
        assert batch.sent == 5
        assert batch.failed == 0
        assert db.get_pending_outbox(pending_notify=True) == []

    # Verify message content and priority flags
    assert len(server.requests) == 5
    unknown_req, zone_req, offline_req, online_req, err_req = server.requests

    assert unknown_req["method"] == "sendPhoto"
    assert unknown_req["has_photo"] is True
    assert unknown_req["disable_notification"] is False
    assert "Type: person_detected" in unknown_req["text"]
    assert "Camera: Driveway Cam" in unknown_req["text"]
    assert "Person: Unknown" in unknown_req["text"]
    assert "Time: " in unknown_req["text"]

    assert zone_req["method"] == "sendPhoto"
    assert zone_req["disable_notification"] is False
    assert "Type: zone_intrusion" in zone_req["text"]
    assert "Front Gate" in zone_req["text"]

    assert offline_req["method"] == "sendMessage"
    assert offline_req["disable_notification"] is False
    assert "camera_offline" in offline_req["text"]

    assert online_req["method"] == "sendMessage"
    assert online_req["disable_notification"] is True
    assert "camera_online" in online_req["text"]

    assert err_req["method"] == "sendMessage"
    assert err_req["disable_notification"] is False
    assert "system_error" in err_req["text"]

    # Force a delivery error and verify bot token never leaks into logs or DB outbox
    server.online = False
    with Database(db_path=settings.db_path, settings=settings) as db:
        EventManager(db, settings=settings).record_system_error("Test outage")
        worker = OutboxWorker(
            db,
            client=client,
            settings=settings,
            rate_limiter=RateLimiter(max_per_minute=60, min_interval_seconds=0.0),
        )
        res = worker.process_once()
        assert res.failed == 1
        pending = db.get_pending_outbox(pending_notify=True)
        assert len(pending) == 1
        assert bot_token not in (pending[0]["last_error"] or "")

    for handler in logging.getLogger().handlers:
        handler.flush()
    log_text = settings.log_path.read_text(encoding="utf-8")
    assert bot_token not in log_text


def test_outbox_worker_retries_during_outage_and_delivers_once_on_recovery(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "outbox_retry.db",
        snapshots_dir=tmp_path / "snapshots",
        camera_name="Porch Cam",
        telegram_bot_token="999:TEST_TOKEN",
        telegram_chat_id="111222",
        telegram_poll_interval=0.05,
        telegram_backoff_initial=0.05,
        telegram_backoff_max=0.1,
        telegram_min_interval_seconds=0.0,
    )
    server = FakeTelegramServer(online=False)
    client = TelegramClient(settings=settings, transport=server.transport)

    with Database(db_path=settings.db_path, settings=settings) as db:
        mgr = EventManager(db, settings=settings)
        mgr.record_zone_intrusion("porch", "Porch", 1, timestamp=1.0)
        mgr.record_person_detected(2, timestamp=2.0)
        mgr.record_camera_offline("cable_unplugged")

        worker = OutboxWorker(
            db,
            client=client,
            settings=settings,
            rate_limiter=RateLimiter(max_per_minute=100, min_interval_seconds=0.0),
        )
        worker.start()
        try:
            # While internet is unplugged, outbox attempts retries and loses zero events
            assert _wait_until(
                lambda: db.get_pending_outbox(pending_notify=True)[0]["notify_attempts"] >= 2,
                timeout=2.0,
            )
            assert len(server.requests) == 0
            assert len(db.get_pending_outbox(pending_notify=True)) == 3

            # Restore internet -> queued alerts arrive once each in order
            server.online = True
            assert _wait_until(
                lambda: len(db.get_pending_outbox(pending_notify=True)) == 0,
                timeout=3.0,
            )
        finally:
            worker.stop()

    assert len(server.requests) == 3
    assert "Type: zone_intrusion" in server.requests[0]["text"]
    assert "Type: person_detected" in server.requests[1]["text"]
    assert "Type: camera_offline" in server.requests[2]["text"]


def test_rate_limiter_enforces_spacing_and_per_minute_cap() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return now[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(
        max_per_minute=2,
        min_interval_seconds=1.0,
        monotonic_fn=fake_monotonic,
        sleep_fn=fake_sleep,
    )

    assert limiter.acquire() is True  # t=0.0
    assert limiter.acquire() is True  # sleeps 1.0 -> t=1.0
    assert limiter.acquire() is True  # window full (2 in 60s), sleeps 59.0 -> t=60.0

    assert sleeps[0] == 1.0
    assert sleeps[1] == 59.0
