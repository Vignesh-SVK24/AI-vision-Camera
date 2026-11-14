"""Unit tests for optional Supabase outbox sync, privacy guards, backoff, and 30-day cleanup."""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image

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
            fake_cv2.resize = lambda img, *_args, **_kwargs: img
            fake_cv2.imwrite = lambda *_args, **_kwargs: True
            fake_cv2.imdecode = lambda *_args, **_kwargs: None
            sys.modules["cv2"] = fake_cv2


_ensure_optional_stubs()

from config.settings import Settings  # noqa: E402
from storage.cloud_sync import (  # noqa: E402
    CloudCleanupResult,
    CloudSyncDeliveryError,
    CloudSyncWorker,
    SupabaseClient,
)
from storage.db import Database  # noqa: E402


class MockSupabaseServer:
    """In-memory fake Supabase REST + Storage HTTP transport for deterministic tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Optional[bytes], dict[str, str]]] = []
        self.rows: dict[int, dict[str, Any]] = {}
        self.objects: dict[str, bytes] = {}
        self.fail_next_storage_upload = False
        self.fail_next_row_upsert = False
        self.fail_next_storage_delete = False

    def __call__(
        self,
        method: str,
        url: str,
        body: Optional[bytes],
        headers: dict[str, str],
        _timeout: float,
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, body, headers))

        if "/storage/v1/object/" in url:
            if method == "POST":
                if self.fail_next_storage_upload:
                    self.fail_next_storage_upload = False
                    raise CloudSyncDeliveryError("Simulated network drop during thumbnail upload")
                prefix = "/storage/v1/object/event-thumbnails/"
                key = url.split(prefix, 1)[1]
                self.objects[key] = body or b""
                return 200, b'{"Key":"' + key.encode("utf-8") + b'"}'

            if method == "DELETE":
                if self.fail_next_storage_delete:
                    self.fail_next_storage_delete = False
                    return 503, b'{"message":"Storage unavailable"}'
                parsed = json.loads((body or b"{}").decode("utf-8"))
                for key in parsed.get("prefixes", []):
                    self.objects.pop(key, None)
                return 200, b"[]"

        if "/rest/v1/events" in url:
            if method == "POST":
                if self.fail_next_row_upsert:
                    self.fail_next_row_upsert = False
                    return 500, b'{"message":"DB timeout"}'
                row = json.loads((body or b"{}").decode("utf-8"))
                self.rows[int(row["local_event_id"])] = row
                return 201, b""

            if method == "GET":
                # Parse event_created_ts=lt.<cutoff>
                cutoff_str = url.split("event_created_ts=lt.", 1)[1].split("&", 1)[0]
                cutoff = float(cutoff_str)
                expired = [
                    {
                        "local_event_id": r["local_event_id"],
                        "thumbnail_path": r.get("thumbnail_path"),
                    }
                    for r in self.rows.values()
                    if float(r["event_created_ts"]) < cutoff
                ]
                return 200, json.dumps(expired).encode("utf-8")

            if method == "DELETE":
                ids_part = url.split("local_event_id=in.(", 1)[1].rstrip(")")
                for raw_id in ids_part.split(","):
                    if raw_id:
                        self.rows.pop(int(raw_id), None)
                return 204, b""

        return 404, b'{"message":"Not found"}'


def _make_jpeg(path: Path, width: int = 640, height: int = 360) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=(41, 82, 85))
    img.save(path, format="JPEG", quality=85)


def _seed_person_and_event(
    db: Database,
    *,
    name: str = "Alice PrivateName",
    snapshot_rel_path: Optional[str] = None,
    event_type: str = "person_detected",
    with_person: bool = True,
) -> int:
    person_id: Optional[int] = None
    if with_person:
        person_id = db.create_person(
            name=name,
            is_trusted=True,
            consent_date="2026-11-01",
            consent_note="Signed household consent",
        )
        db.add_face_embedding(
            person_id=person_id,
            embedding_bytes=b"\x01\x02\x03\x04",
            model_name="sface_test",
        )
        db.add_person_photo(
            person_id=person_id,
            file_path="faces_data/alice_secret_crop.jpg",
        )

    event_id, _ = db.insert_event(
        event_type=event_type,
        track_id=7,
        zone_id="porch" if event_type == "zone_intrusion" else None,
        zone_name="Front Porch" if event_type == "zone_intrusion" else None,
        person_id=person_id,
        confidence=0.91,
        snapshot_path=snapshot_rel_path,
        message=f"Sensitive internal note mentioning {name}",
    )
    return event_id


class TestCloudSync(unittest.TestCase):
    def test_disabled_by_default_makes_zero_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
                cloud_sync=False,
                supabase_url="https://example.supabase.co",
                supabase_key="test-service-key",
            )
            server = MockSupabaseServer()
            client = SupabaseClient(settings=cfg, transport=server)

            with Database(db_path=cfg.db_path, settings=cfg) as db:
                snap = cfg.snapshots_dir / "2026-11-12" / "event_1.jpg"
                _make_jpeg(snap)
                _seed_person_and_event(db, snapshot_rel_path="snapshots/2026-11-12/event_1.jpg")

                worker = CloudSyncWorker(db, client=client, settings=cfg, base_dir=root)
                res = worker.process_once()

                self.assertTrue(res.skipped_disabled)
                self.assertEqual(res.synced, 0)
                self.assertEqual(server.calls, [])
                pending = db.get_pending_outbox(pending_sync=True)
                self.assertEqual(len(pending), 1)

    def test_sync_uploads_thumbnail_and_privacy_filtered_row_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
                cloud_sync=True,
                supabase_url="https://example.supabase.co",
                supabase_key="super-secret-supabase-key",
                cloud_sync_thumbnail_max_width=320,
            )
            server = MockSupabaseServer()
            client = SupabaseClient(settings=cfg, transport=server)

            with Database(db_path=cfg.db_path, settings=cfg) as db:
                snap = cfg.snapshots_dir / "2026-11-12" / "ev1.jpg"
                _make_jpeg(snap, width=800, height=450)
                ev1 = _seed_person_and_event(
                    db,
                    name="Alice PrivateName",
                    snapshot_rel_path="snapshots/2026-11-12/ev1.jpg",
                    with_person=True,
                )

                # Also seed an adversarial path inside faces_data_dir — must be refused!
                bad_face = cfg.faces_data_dir / "alice_crop.jpg"
                _make_jpeg(bad_face, width=200, height=200)
                ev2 = _seed_person_and_event(
                    db,
                    snapshot_rel_path="faces_data/alice_crop.jpg",
                    event_type="zone_intrusion",
                    with_person=False,
                )

                worker = CloudSyncWorker(db, client=client, settings=cfg, base_dir=root)
                res = worker.process_once()

                self.assertFalse(res.skipped_disabled)
                self.assertEqual(res.synced, 2)
                self.assertEqual(res.failed, 0)
                self.assertEqual(db.get_pending_outbox(pending_sync=True), [])

                # Event 1 thumbnail was resized to <=320px and uploaded; Event 2 face crop was NOT uploaded
                self.assertEqual(len(server.objects), 1)
                uploaded_key = next(iter(server.objects))
                self.assertTrue(uploaded_key.endswith(f"event_{ev1}.jpg"))
                with Image.open(
                    importlib.import_module("io").BytesIO(server.objects[uploaded_key])
                ) as thumb:
                    self.assertLessEqual(thumb.width, 320)

                row1 = server.rows[ev1]
                row2 = server.rows[ev2]
                self.assertEqual(row1["person_label"], "Known")
                self.assertEqual(row2["person_label"], "Unknown")
                self.assertIsNone(row2["thumbnail_path"])

                serialized = json.dumps(server.rows)
                self.assertNotIn("Alice PrivateName", serialized)
                self.assertNotIn("consent", serialized.lower())
                self.assertNotIn("embedding", serialized.lower())
                self.assertNotIn("faces_data", serialized.lower())

    def test_retries_with_backoff_and_marks_synced_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
                cloud_sync=True,
                supabase_url="https://example.supabase.co",
                supabase_key="super-secret-supabase-key",
                cloud_sync_backoff_initial=2.0,
                cloud_sync_backoff_max=16.0,
            )
            server = MockSupabaseServer()
            client = SupabaseClient(settings=cfg, transport=server)

            with Database(db_path=cfg.db_path, settings=cfg) as db:
                snap = cfg.snapshots_dir / "2026-11-12" / "ev_retry.jpg"
                _make_jpeg(snap)
                ev_id = _seed_person_and_event(
                    db, snapshot_rel_path="snapshots/2026-11-12/ev_retry.jpg"
                )
                worker = CloudSyncWorker(db, client=client, settings=cfg, base_dir=root)

                # Fail on storage upload -> must remain unsynced
                server.fail_next_storage_upload = True
                r1 = worker.process_once()
                self.assertEqual((r1.synced, r1.failed), (0, 1))
                pending1 = db.get_pending_outbox(pending_sync=True)
                self.assertEqual(len(pending1), 1)
                self.assertEqual(pending1[0]["sync_attempts"], 1)

                # Succeed storage upload, fail PostgREST row upsert -> still unsynced
                server.fail_next_row_upsert = True
                r2 = worker.process_once()
                self.assertEqual((r2.synced, r2.failed), (0, 1))
                pending2 = db.get_pending_outbox(pending_sync=True)
                self.assertEqual(len(pending2), 1)
                self.assertEqual(pending2[0]["sync_attempts"], 2)
                self.assertNotIn("super-secret-supabase-key", pending2[0]["last_error"] or "")

                # Backoff schedule doubles cleanly up to max
                self.assertEqual(worker._next_backoff_delay(0), 2.0)
                self.assertEqual(worker._next_backoff_delay(1), 4.0)
                self.assertEqual(worker._next_backoff_delay(2), 8.0)
                self.assertEqual(worker._next_backoff_delay(4), 16.0)

                # Recovery attempt succeeds -> synced=1
                r3 = worker.process_once()
                self.assertEqual((r3.synced, r3.failed), (1, 0))
                self.assertEqual(db.get_pending_outbox(pending_sync=True), [])
                self.assertIn(ev_id, server.rows)

    def test_daily_cleanup_deletes_storage_thumbnails_before_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = Settings(
                db_path=root / "test.db",
                snapshots_dir=root / "snapshots",
                faces_data_dir=root / "faces_data",
                cloud_sync=True,
                supabase_url="https://example.supabase.co",
                supabase_key="test-key",
                cloud_sync_retention_days=30,
                cloud_sync_cleanup_hour=3,
            )
            server = MockSupabaseServer()
            client = SupabaseClient(settings=cfg, transport=server)

            now = datetime(2026, 11, 12, 4, 0, 0, tzinfo=timezone.utc)
            old_ts = (now - timedelta(days=35)).timestamp()
            recent_ts = (now - timedelta(days=5)).timestamp()

            server.rows[101] = {
                "local_event_id": 101,
                "event_created_ts": old_ts,
                "thumbnail_path": "events/2026-10-08/event_101.jpg",
            }
            server.rows[102] = {
                "local_event_id": 102,
                "event_created_ts": recent_ts,
                "thumbnail_path": "events/2026-11-07/event_102.jpg",
            }
            server.objects["events/2026-10-08/event_101.jpg"] = b"old-jpg"
            server.objects["events/2026-11-07/event_102.jpg"] = b"fresh-jpg"

            with Database(db_path=cfg.db_path, settings=cfg) as db:
                worker = CloudSyncWorker(db, client=client, settings=cfg, base_dir=root)

                # If storage delete fails, DB row must NOT be deleted (no orphaned blobs)
                server.fail_next_storage_delete = True
                with self.assertRaises(CloudSyncDeliveryError):
                    worker.cleanup_if_due(now=now)
                self.assertIn(101, server.rows)
                self.assertIn("events/2026-10-08/event_101.jpg", server.objects)

                # Successful daily cleanup deletes both expired storage file and DB row
                result = worker.cleanup_if_due(now=now)
                self.assertEqual(result, CloudCleanupResult(rows_deleted=1, thumbnails_deleted=1))
                self.assertNotIn(101, server.rows)
                self.assertNotIn("events/2026-10-08/event_101.jpg", server.objects)
                self.assertIn(102, server.rows)
                self.assertIn("events/2026-11-07/event_102.jpg", server.objects)

                # Calling again same UTC day is a no-op
                self.assertIsNone(worker.cleanup_if_due(now=now + timedelta(hours=2)))


if __name__ == "__main__":
    unittest.main()
