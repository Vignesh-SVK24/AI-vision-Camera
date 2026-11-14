"""Reliability & chaos tests simulating internet loss, camera loss, frame stalls, low disk, and PC restart."""

from __future__ import annotations

import csv
import importlib
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_optional_stubs() -> None:
    if "dotenv" not in sys.modules:
        try:
            importlib.import_module("dotenv")
        except ImportError:
            fake_dotenv = types.ModuleType("dotenv")
            fake_dotenv.load_dotenv = lambda *_args, **_kwargs: False
            sys.modules["dotenv"] = fake_dotenv

    if "cv2" not in sys.modules:
        try:
            importlib.import_module("cv2")
        except ImportError:
            fake_cv2 = types.ModuleType("cv2")
            fake_cv2.IMWRITE_JPEG_QUALITY = 1
            fake_cv2.INTER_AREA = 3
            fake_cv2.IMREAD_COLOR = 1
            fake_cv2.CAP_FFMPEG = 1900
            fake_cv2.CAP_PROP_BUFFERSIZE = 38
            fake_cv2.resize = lambda img, *_args, **_kwargs: img

            def _fake_imwrite(path_str: str, _img: Any, *_args: Any) -> bool:
                Path(path_str).write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
                return True

            fake_cv2.imwrite = _fake_imwrite
            fake_cv2.imdecode = lambda *_args, **_kwargs: None
            sys.modules["cv2"] = fake_cv2


_ensure_optional_stubs()

from camera.rtsp_client import RTSPCamera  # noqa: E402
from config.settings import Settings  # noqa: E402
from events.event_manager import EventManager  # noqa: E402
from notifications.telegram import (  # noqa: E402
    OutboxWorker,
    RateLimiter,
    TelegramClient,
    TelegramDeliveryError,
)
from storage.cloud_sync import (  # noqa: E402
    CloudSyncDeliveryError,
    CloudSyncWorker,
    SupabaseClient,
)
from storage.db import Database  # noqa: E402
from storage.reliability import (  # noqa: E402
    DiskSpaceGuard,
    EngineSupervisor,
    FrameWatchdog,
    GracefulShutdown,
)
from tools.backup_db import backup_sqlite_database  # noqa: E402
from tools.soak_test import run_soak_test  # noqa: E402


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestReliabilityAndSoak(unittest.TestCase):
    def test_internet_loss_and_recovery_zero_lost_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
                telegram_enabled=True,
                telegram_bot_token="123456:fake-token",
                telegram_chat_id="99999",
                cloud_sync=True,
                supabase_url="https://example.supabase.co",
                supabase_key="fake-service-key",
            )
            internet_up = {"ok": False}
            tg_sent: list[bytes] = []
            sb_synced: list[str] = []

            def fake_tg_transport(_url: str, body: bytes, _headers: dict[str, str], _timeout: float):
                if not internet_up["ok"]:
                    raise TelegramDeliveryError("Internet offline")
                tg_sent.append(body)
                return 200, b'{"ok":true,"result":{"message_id":1}}'

            def fake_sb_transport(
                method: str, url: str, body: Optional[bytes], _headers: dict[str, str], _timeout: float
            ):
                if not internet_up["ok"]:
                    raise CloudSyncDeliveryError("Internet offline")
                sb_synced.append(f"{method} {url}")
                return 201, b"{}"

            with Database(db_path=cfg.db_path, settings=cfg) as db:
                db.insert_event(event_type="zone_intrusion", zone_id="z1", zone_name="Gate")
                db.insert_event(event_type="person_detected", track_id=3)

                tg_worker = OutboxWorker(
                    db,
                    client=TelegramClient(settings=cfg, transport=fake_tg_transport),
                    settings=cfg,
                    rate_limiter=RateLimiter(max_per_minute=100, min_interval_seconds=0.0),
                    base_dir=root,
                )
                sb_worker = CloudSyncWorker(
                    db,
                    client=SupabaseClient(settings=cfg, transport=fake_sb_transport),
                    settings=cfg,
                    base_dir=root,
                )

                # While internet is down, nothing is lost and nothing is marked delivered
                r_tg_down = tg_worker.process_once()
                r_sb_down = sb_worker.process_once()
                self.assertEqual(r_tg_down.failed, 1)
                self.assertEqual(r_sb_down.failed, 1)
                self.assertEqual(len(db.get_pending_outbox(pending_notify=True)), 2)
                self.assertEqual(len(db.get_pending_outbox(pending_sync=True)), 2)

                # Internet returns -> queued outbox delivers cleanly
                internet_up["ok"] = True
                r_tg_up = tg_worker.process_once()
                r_sb_up = sb_worker.process_once()
                self.assertEqual(r_tg_up.sent, 2)
                self.assertEqual(r_sb_up.synced, 2)
                self.assertEqual(db.get_pending_outbox(pending_notify=True), [])
                self.assertEqual(db.get_pending_outbox(pending_sync=True), [])
                self.assertEqual(len(tg_sent), 2)
                self.assertEqual(len(sb_synced), 2)

    def test_camera_loss_recovery_and_frame_stall_watchdog_restart(self) -> None:
        # 1. Simulate RTSPCamera disconnect + reconnect events
        state = {"phase": "fail", "opened": 0}
        events_seen: list[str] = []

        class _FakeCap:
            def __init__(self, fail_read: bool) -> None:
                self.fail_read = fail_read
                self.reads = 0

            def isOpened(self) -> bool:
                return True

            def read(self):
                self.reads += 1
                if self.fail_read:
                    return False, None
                return True, np.zeros((32, 32, 3), dtype=np.uint8)

            def release(self) -> None:
                pass

        def cap_factory(_src: Any, _rtsp: bool):
            state["opened"] += 1
            if state["phase"] == "fail":
                state["phase"] = "ok"
                return _FakeCap(fail_read=True)
            return _FakeCap(fail_read=False)

        cam = RTSPCamera(
            source="rtsp://127.0.0.1:554/stream2",
            reconnect_initial_delay=0.02,
            reconnect_max_delay=0.05,
            stale_timeout=2.0,
            capture_factory=cap_factory,
            on_event=lambda name, _details: events_seen.append(name),
        )
        cam.start()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ok, frame = cam.read()
            if ok and frame is not None and "connected" in events_seen:
                break
            time.sleep(0.02)
        cam.stop()
        self.assertIn("disconnected", events_seen)
        self.assertIn("connected", events_seen)

        # 2. Simulate frame stall triggering EngineSupervisor restart, then graceful shutdown
        shutdown = GracefulShutdown()
        runs = {"count": 0}
        stalls: list[float] = []

        def worker(worker_stop: threading.Event, watchdog: FrameWatchdog) -> int:
            runs["count"] += 1
            if runs["count"] == 1:
                # Simulate hung pipeline (never calls watchdog.mark_frame())
                worker_stop.wait(2.0)
                return 0
            # Healthy second run marks frames and triggers clean shutdown
            watchdog.mark_frame()
            shutdown.trigger("test complete")
            return 0

        sup = EngineSupervisor(
            worker,
            shutdown=shutdown,
            stall_timeout_seconds=0.08,
            check_interval_seconds=0.02,
            restart_backoff_initial=0.02,
            on_stall=lambda elapsed: stalls.append(elapsed),
        )
        code = sup.run()
        self.assertEqual(code, 0)
        self.assertGreaterEqual(runs["count"], 2)
        self.assertEqual(sup.stall_restarts, 1)
        self.assertEqual(len(stalls), 1)

    def test_pc_restart_persistence_and_graceful_shutdown_lifo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "surveillance.db"
            order: list[str] = []

            shutdown = GracefulShutdown()
            shutdown.register(lambda: order.append("first_registered"))
            shutdown.register(lambda: (_ for _ in ()).throw(RuntimeError("ignore hook error")))
            shutdown.register(lambda: order.append("last_registered"))

            # First "boot": insert event, trigger shutdown & close DB cleanly
            db1 = Database(db_path=db_path)
            shutdown.register(db1.close)
            ev_id, _ = db1.insert_event(event_type="camera_online", message="Boot #1 online")
            shutdown.trigger("reboot")
            shutdown.run_cleanup()
            self.assertEqual(order, ["last_registered", "first_registered"])

            # Second "boot": fresh Database handle sees persisted events/outbox intact
            with Database(db_path=db_path) as db2:
                loaded = db2.get_event(ev_id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["message"], "Boot #1 online")
                self.assertEqual(len(db2.get_pending_outbox(pending_notify=True)), 1)

    def test_disk_space_guard_alerts_once_and_stops_snapshots_before_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
            )
            clock = FakeClock()
            free_mb_box = {"mb": 5000.0}
            alerts: list[tuple[str, str]] = []

            guard = DiskSpaceGuard(
                cfg.snapshots_dir,
                low_warn_mb=2048.0,
                critical_stop_mb=512.0,
                check_interval_seconds=0.0,
                on_alert=lambda level, msg: alerts.append((level, msg)),
                disk_usage_fn=lambda _p: (
                    100_000 * 1024 * 1024,
                    0,
                    int(free_mb_box["mb"] * 1024 * 1024),
                ),
                monotonic_fn=clock,
            )

            frame = np.zeros((40, 40, 3), dtype=np.uint8)
            with Database(db_path=cfg.db_path, settings=cfg) as db:
                mgr = EventManager(db, settings=cfg, disk_guard=guard)

                # Healthy -> snapshot saved
                ev_ok = mgr.record_person_detected(1, frame=frame)
                self.assertIsNotNone(db.get_event(ev_ok)["snapshot_path"])
                self.assertEqual(alerts, [])

                # Low warning -> 1 alert emitted (no repeat spam), snapshot still saved
                free_mb_box["mb"] = 1024.0
                ev_low1 = mgr.record_person_detected(2, frame=frame)
                ev_low2 = mgr.record_person_detected(3, frame=frame)
                self.assertIsNotNone(db.get_event(ev_low1)["snapshot_path"])
                self.assertIsNotNone(db.get_event(ev_low2)["snapshot_path"])
                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0][0], "low")

                # Critical -> critical alert emitted, snapshot saving stopped, event row still recorded
                free_mb_box["mb"] = 200.0
                ev_crit = mgr.record_person_detected(4, frame=frame)
                self.assertIsNone(db.get_event(ev_crit)["snapshot_path"])
                self.assertEqual(len(alerts), 2)
                self.assertEqual(alerts[1][0], "critical")

                # Recovery resets warning flags
                free_mb_box["mb"] = 5000.0
                ev_rec = mgr.record_person_detected(5, frame=frame)
                self.assertIsNotNone(db.get_event(ev_rec)["snapshot_path"])

    def test_weekly_hot_backup_and_soak_logger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "surveillance.db"
            backup_dir = root / "external_drive_backups"
            soak_csv = root / "logs" / "soak.csv"

            # Populate WAL database
            with Database(db_path=db_path) as db:
                db.insert_event(event_type="camera_online", message="Before backup")

                # Perform 3 hot backups with keep_count=2
                t0 = datetime(2026, 11, 1, 3, 30, 0, tzinfo=timezone.utc)
                b1 = backup_sqlite_database(db_path, backup_dir, keep_count=2, timestamp=t0)
                b2 = backup_sqlite_database(
                    db_path, backup_dir, keep_count=2, timestamp=t0 + timedelta(days=7)
                )
                b3 = backup_sqlite_database(
                    db_path, backup_dir, keep_count=2, timestamp=t0 + timedelta(days=14)
                )

            self.assertFalse(b1.exists())  # pruned
            self.assertTrue(b2.exists())
            self.assertTrue(b3.exists())

            check_conn = sqlite3.connect(str(b3))
            try:
                self.assertEqual(check_conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                cnt = check_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                self.assertEqual(cnt, 1)
            finally:
                check_conn.close()

            # Run a short soak test (3 samples with synthetic events)
            summary = run_soak_test(
                db_path=db_path,
                output_csv=soak_csv,
                duration_seconds=5.0,
                interval_seconds=0.01,
                max_iterations=3,
                simulate_events=True,
            )
            self.assertEqual(summary.samples, 3)
            with soak_csv.open("r", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 3)
            self.assertEqual(int(rows[-1]["events_total"]), 4)


if __name__ == "__main__":
    unittest.main()
