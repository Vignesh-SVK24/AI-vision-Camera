"""Tests for SQLite WAL storage, migrations, snapshots, cooldowns, outbox, and retention."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from config.logging_config import setup_logging
from config.settings import Settings
from events.event_manager import (
    EVENT_CAMERA_OFFLINE,
    EVENT_CAMERA_ONLINE,
    EVENT_PERSON_DETECTED,
    EVENT_SYSTEM_ERROR,
    EVENT_ZONE_INTRUSION,
    EventManager,
)
from storage.db import Database
from storage.retention import NightlyRetentionScheduler, run_retention
from storage.snapshots import resolve_snapshot_path
from tracking.tracker import TrackedPerson
from tracking.zones import ZoneEvent


def test_sqlite_wal_mode_tables_and_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with Database(db_path=db_path) as db:
        assert db.journal_mode() == "wal"
        assert db.schema_version() >= 2
        tables = db.table_names()
        expected = {
            "events",
            "people",
            "face_embeddings",
            "outbox",
            "audit_log",
            "settings",
            "schema_migrations",
        }
        assert expected.issubset(tables)

        db.set_setting("camera_name", "front_gate")
        assert db.get_setting("camera_name") == "front_gate"


def test_all_event_types_outbox_and_snapshot_resize(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "events.db",
        snapshots_dir=tmp_path / "snapshots",
        snapshot_max_width=480,
        snapshot_quality=80,
        camera_pass="SecretCamPass!",
    )
    large_frame = np.full((720, 1280, 3), 120, dtype=np.uint8)

    with Database(db_path=settings.db_path, settings=settings) as db:
        mgr = EventManager(db, settings=settings)

        e1 = mgr.record_person_detected(1, confidence=0.92, frame=large_frame)
        e2 = mgr.record_zone_intrusion("z1", "Driveway", 1, dwell_seconds=1.5, frame=large_frame)
        e3 = mgr.record_camera_offline("stream_lost", source="rtsp://admin:SecretCamPass!@1.2.3.4/stream2")
        e4 = mgr.record_camera_online(source="rtsp://admin:SecretCamPass!@1.2.3.4/stream2")
        e5 = mgr.record_system_error("Failure on rtsp://admin:SecretCamPass!@1.2.3.4/stream2")

        assert all(eid is not None for eid in (e1, e2, e3, e4, e5))

        events = db.list_events(limit=10)
        assert {ev["event_type"] for ev in events} == {
            EVENT_PERSON_DETECTED,
            EVENT_ZONE_INTRUSION,
            EVENT_CAMERA_OFFLINE,
            EVENT_CAMERA_ONLINE,
            EVENT_SYSTEM_ERROR,
        }

        # Verify password was masked before persisting system_error / camera events
        for ev in events:
            assert "SecretCamPass!" not in (ev["message"] or "")
            assert "SecretCamPass!" not in (ev["metadata_json"] or "")

        # Verify snapshot resized to <= 480px wide and stored in YYYY-MM-DD folder
        ev_person = db.get_event(e1)
        assert ev_person is not None and ev_person["snapshot_path"]
        snap_file = resolve_snapshot_path(ev_person["snapshot_path"])
        assert snap_file.exists()
        assert snap_file.suffix.lower() == ".jpg"
        assert len(snap_file.parent.name) == 10  # YYYY-MM-DD
        loaded = cv2.imread(str(snap_file))
        assert loaded is not None
        assert loaded.shape[1] == 480
        assert loaded.shape[0] == 270  # 1280x720 aspect ratio preserved

        # Verify outbox flags
        pending = db.get_pending_outbox(pending_notify=True, pending_sync=True)
        assert len(pending) == 5
        first_outbox_id = pending[0]["id"]
        db.mark_outbox_notified(first_outbox_id)
        db.mark_outbox_synced(first_outbox_id)
        still_unnotified = db.get_pending_outbox(pending_notify=True)
        assert len(still_unnotified) == 4


def test_continuous_person_and_cooldown_prevents_spam(tmp_path: Path) -> None:
    clock = [0.0]
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "cooldown.db",
        snapshots_dir=tmp_path / "snapshots",
        event_track_cooldown_seconds=60.0,
        event_zone_cooldown_seconds=60.0,
        event_absence_reset_seconds=5.0,
    )
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    standing_person = TrackedPerson(track_id=10, bbox=(50, 50, 120, 200), confidence=0.9)

    with Database(db_path=settings.db_path, settings=settings) as db:
        mgr = EventManager(db, settings=settings, monotonic_fn=lambda: clock[0])

        # Person stands continuously in view for 180 seconds (200 frames): ONLY 1 event!
        emitted_total = 0
        for step in range(200):
            clock[0] = step * 0.9
            emitted = mgr.process_tracks([standing_person], frame=frame)
            emitted_total += len(emitted)

        assert emitted_total == 1
        assert len(db.list_events(event_type=EVENT_PERSON_DETECTED)) == 1

        # Person leaves view for 10 seconds (> absence_reset_seconds), still within 60s?
        # Here last event was at t=0.0 and now t=200*0.9=180.0 (> 60s), so absence + return emits 1 new event.
        clock[0] += 10.0
        assert mgr.process_tracks([], frame=frame) == []
        clock[0] += 1.0
        reappeared = mgr.process_tracks([standing_person], frame=frame)
        assert len(reappeared) == 1

        # Direct zone intrusion cooldown check: suppressed within 60s, allowed after 60s
        z_ev = ZoneEvent(
            event_type="enter",
            zone_id="door",
            zone_name="Front Door",
            track_id=99,
            timestamp=clock[0],
            dwell_seconds=1.0,
            anchor_point=(100, 180),
            bbox=(80, 100, 120, 180),
        )
        assert mgr.process_tracks([], zone_events=[z_ev], timestamp=1000.0) != []
        assert mgr.process_tracks([], zone_events=[z_ev], timestamp=1030.0) == []
        assert mgr.process_tracks([], zone_events=[z_ev], timestamp=1061.0) != []


def test_retention_deletes_old_events_and_snapshots_and_logs(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "retention.db",
        snapshots_dir=tmp_path / "snapshots",
        retention_days=30,
    )
    setup_logging(settings)

    now = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)
    old_dt = now - timedelta(days=35)
    recent_dt = now - timedelta(days=5)
    frame = np.full((120, 160, 3), 200, dtype=np.uint8)

    with Database(db_path=settings.db_path, settings=settings) as db:
        mgr = EventManager(db, settings=settings)
        old_id = mgr.record_person_detected(1, frame=frame, timestamp=1.0, created_at=old_dt)
        recent_id = mgr.record_person_detected(2, frame=frame, timestamp=2.0, created_at=recent_dt)
        assert old_id is not None and recent_id is not None

        old_ev = db.get_event(old_id)
        recent_ev = db.get_event(recent_id)
        old_snap = resolve_snapshot_path(old_ev["snapshot_path"])
        recent_snap = resolve_snapshot_path(recent_ev["snapshot_path"])
        assert old_snap.exists()
        assert recent_snap.exists()

        report = run_retention(
            db,
            retention_days=30,
            snapshots_dir=settings.snapshots_dir,
            now=now,
        )

        assert report.deleted_events == 1
        assert report.deleted_outbox == 1
        assert report.deleted_snapshots == 1
        assert report.removed_empty_dirs == 1

        assert db.get_event(old_id) is None
        assert db.get_event(recent_id) is not None
        assert not old_snap.exists()
        assert recent_snap.exists()
        assert len(db.get_pending_outbox()) == 1

        # Verify nightly scheduler starts and stops cleanly
        scheduler = NightlyRetentionScheduler(db, settings=settings)
        scheduler.start()
        assert scheduler.is_running is True
        scheduler.stop()
        assert scheduler.is_running is False

    for handler in logging.getLogger().handlers:
        handler.flush()
    log_text = settings.log_path.read_text(encoding="utf-8")
    assert "Retention cleanup complete" in log_text
    assert "removed 1 event(s)" in log_text
