"""Optional off-site Supabase mirror for event history and small thumbnails (off by default)."""

from __future__ import annotations

import io
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from config.logging_config import mask_sensitive_text
from config.settings import BASE_DIR, Settings, get_settings
from storage.db import Database
from storage.snapshots import resolve_snapshot_path

logger = logging.getLogger(__name__)

# (method, url, body_bytes_or_none, headers, timeout_seconds) -> (status_code, response_bytes)
SupabaseTransport = Callable[
    [str, str, Optional[bytes], dict[str, str], float],
    tuple[int, bytes],
]

HIGH_PRIVACY_EXCLUDED_FIELDS = frozenset(
    {
        "person_name",
        "face_embeddings",
        "embedding",
        "consent_date",
        "consent_note",
        "photo_count",
        "face_crops",
        "faces_data",
    }
)


class CloudSyncError(RuntimeError):
    """Base exception for Supabase cloud sync failures."""


class CloudSyncDeliveryError(CloudSyncError):
    """Raised on transient network or API errors during Supabase sync."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class CloudSyncBatchResult:
    synced: int
    failed: int
    skipped_disabled: bool = False


@dataclass(frozen=True)
class CloudCleanupResult:
    rows_deleted: int
    thumbnails_deleted: int


def default_supabase_transport(
    method: str,
    url: str,
    body: Optional[bytes],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    """Send an HTTP request to Supabase REST/Storage APIs using urllib."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        raw_body = b""
        try:
            raw_body = exc.read()
        except Exception:
            pass
        return int(exc.code), raw_body
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise CloudSyncDeliveryError(f"Network error reaching Supabase API: {exc}") from exc


def _parse_event_timestamp(iso_str: str) -> float:
    text = (iso_str or "").strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return datetime.now(timezone.utc).timestamp()


def _is_within_directory(target: Path, parent: Path) -> bool:
    try:
        target.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_safe_thumbnail_bytes(
    stored_path: Optional[str],
    settings: Settings,
    *,
    base_dir: Path = BASE_DIR,
) -> Optional[bytes]:
    """Read and resize an event snapshot into a small JPEG thumbnail.

    Refuses any file under `faces_data_dir` or outside `snapshots_dir`.
    """
    if not stored_path:
        return None

    candidate = resolve_snapshot_path(stored_path, base_dir=base_dir)
    if _is_within_directory(candidate, settings.faces_data_dir):
        logger.warning(
            "Refusing to upload file inside faces_data_dir (%s).",
            candidate.name,
        )
        return None
    if not _is_within_directory(candidate, settings.snapshots_dir):
        logger.warning(
            "Refusing to upload file outside snapshots_dir (%s).",
            candidate.name,
        )
        return None
    if not candidate.is_file():
        return None

    raw = candidate.read_bytes()
    max_width = max(64, int(settings.cloud_sync_thumbnail_max_width))
    quality = max(30, min(95, int(settings.cloud_sync_thumbnail_quality)))

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            if w > max_width:
                new_h = max(1, int(round(h * (max_width / float(w)))))
                rgb = rgb.resize((max_width, new_h), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            rgb.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception:
        # Fallback if Pillow is unavailable or image decoding fails
        return raw


def _derive_person_label(item: dict[str, Any]) -> Optional[str]:
    """Return a non-identifying simple label only (`Known`, `Unknown`, or None)."""
    if item.get("person_id") is not None or bool(item.get("person_name")):
        return "Known"
    event_type = str(item.get("event_type", ""))
    if event_type in ("person_detected", "zone_intrusion"):
        return "Unknown"
    return None


def build_cloud_event_payload(
    item: dict[str, Any],
    *,
    camera_name: str,
    thumbnail_path: Optional[str] = None,
) -> dict[str, Any]:
    """Build privacy-filtered event row for Supabase (never face photos/embeddings/names)."""
    created_at = str(item.get("event_created_at") or item.get("created_at") or "")
    return {
        "local_event_id": int(item["event_id"]),
        "camera_name": camera_name,
        "event_type": str(item["event_type"]),
        "event_created_at": created_at,
        "event_created_ts": _parse_event_timestamp(created_at),
        "zone_id": item.get("zone_id"),
        "zone_name": item.get("zone_name"),
        "person_label": _derive_person_label(item),
        "confidence": float(item["confidence"]) if item.get("confidence") is not None else None,
        "thumbnail_path": thumbnail_path,
    }


def build_thumbnail_object_path(item: dict[str, Any]) -> str:
    """Deterministic storage object key inside the Supabase thumbnail bucket."""
    created_at = str(item.get("event_created_at") or item.get("created_at") or "")
    date_prefix = created_at[:10] if len(created_at) >= 10 and created_at[4] == "-" else "unknown-date"
    event_id = int(item["event_id"])
    return f"events/{date_prefix}/event_{event_id}.jpg"


class SupabaseClient:
    """Minimal HTTPS client for Supabase PostgREST and Storage APIs."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        transport: Optional[SupabaseTransport] = None,
        timeout: float = 15.0,
    ) -> None:
        cfg = settings or get_settings()
        self._settings = cfg
        self._url = cfg.supabase_url.rstrip("/")
        self._key = cfg.supabase_key
        self._bucket = cfg.supabase_bucket
        self._table = cfg.supabase_table
        self._transport = transport or default_supabase_transport
        self._timeout = float(timeout)

    @property
    def is_configured(self) -> bool:
        return bool(self._url and self._key)

    def _auth_headers(self, *, content_type: Optional[str] = None) -> dict[str, str]:
        if not self.is_configured:
            raise CloudSyncDeliveryError("Supabase URL or key is not configured.")
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, bytes]:
        url = f"{self._url}{path}"
        merged = self._auth_headers()
        if headers:
            merged.update(headers)
        status_code, resp_bytes = self._transport(method, url, body, merged, self._timeout)
        if status_code >= 400:
            snippet = mask_sensitive_text(
                resp_bytes.decode("utf-8", errors="replace")[:240],
                self._settings.secrets(),
            )
            raise CloudSyncDeliveryError(
                f"Supabase {method} {path} failed (HTTP {status_code}): {snippet}"
            )
        return status_code, resp_bytes

    def upload_thumbnail(self, object_path: str, jpeg_bytes: bytes) -> str:
        """Upload a small JPEG thumbnail into the private Supabase Storage bucket."""
        clean_path = "/".join(urllib.parse.quote(seg, safe="") for seg in object_path.split("/"))
        bucket = urllib.parse.quote(self._bucket, safe="")
        self._request(
            "POST",
            f"/storage/v1/object/{bucket}/{clean_path}",
            body=jpeg_bytes,
            headers={"Content-Type": "image/jpeg", "x-upsert": "true"},
        )
        return object_path

    def upsert_event(self, payload: dict[str, Any]) -> None:
        """Upsert a single sanitized event row via PostgREST."""
        table = urllib.parse.quote(self._table, safe="")
        body = json.dumps(payload).encode("utf-8")
        self._request(
            "POST",
            f"/rest/v1/{table}?on_conflict=local_event_id",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )

    def list_expired_events(
        self, cutoff_ts: float, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Fetch expired cloud event IDs and thumbnail object paths."""
        table = urllib.parse.quote(self._table, safe="")
        _, raw = self._request(
            "GET",
            (
                f"/rest/v1/{table}"
                f"?select=local_event_id,thumbnail_path"
                f"&event_created_ts=lt.{cutoff_ts}"
                f"&limit={int(limit)}"
            ),
        )
        data = json.loads(raw.decode("utf-8") or "[]")
        return data if isinstance(data, list) else []

    def delete_storage_objects(self, object_paths: list[str]) -> int:
        """Delete thumbnail files from the Supabase Storage bucket."""
        clean_paths = [p for p in object_paths if p]
        if not clean_paths:
            return 0
        bucket = urllib.parse.quote(self._bucket, safe="")
        body = json.dumps({"prefixes": clean_paths}).encode("utf-8")
        self._request(
            "DELETE",
            f"/storage/v1/object/{bucket}",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        return len(clean_paths)

    def delete_event_rows(self, local_event_ids: list[int]) -> int:
        """Delete expired event rows by local_event_id."""
        if not local_event_ids:
            return 0
        table = urllib.parse.quote(self._table, safe="")
        id_list = ",".join(str(int(eid)) for eid in local_event_ids)
        self._request("DELETE", f"/rest/v1/{table}?local_event_id=in.({id_list})")
        return len(local_event_ids)

    def cleanup_older_than(
        self,
        days: int = 30,
        *,
        now: Optional[datetime] = None,
        batch_size: int = 200,
    ) -> CloudCleanupResult:
        """Delete cloud events older than `days`, removing Storage thumbnails first."""
        ref_now = (
            now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        )
        cutoff_ts = (ref_now - timedelta(days=max(1, int(days)))).timestamp()

        total_rows = 0
        total_thumbs = 0
        while True:
            expired = self.list_expired_events(cutoff_ts, limit=batch_size)
            if not expired:
                break

            thumb_paths = [
                str(row["thumbnail_path"])
                for row in expired
                if row.get("thumbnail_path")
            ]
            # Delete storage objects BEFORE database rows so failed storage calls do not orphan blobs.
            if thumb_paths:
                total_thumbs += self.delete_storage_objects(thumb_paths)

            event_ids = [int(row["local_event_id"]) for row in expired]
            total_rows += self.delete_event_rows(event_ids)

            if len(expired) < batch_size:
                break

        return CloudCleanupResult(rows_deleted=total_rows, thumbnails_deleted=total_thumbs)


class CloudSyncWorker:
    """Outbox-driven background worker mirroring events & thumbnails to Supabase."""

    def __init__(
        self,
        db: Database,
        client: Optional[SupabaseClient] = None,
        settings: Optional[Settings] = None,
        *,
        poll_interval: Optional[float] = None,
        backoff_initial: Optional[float] = None,
        backoff_max: Optional[float] = None,
        base_dir: Path = BASE_DIR,
    ) -> None:
        cfg = settings or get_settings()
        self._db = db
        self._settings = cfg
        self._client = client or SupabaseClient(settings=cfg)
        self._poll_interval = (
            cfg.cloud_sync_poll_interval if poll_interval is None else float(poll_interval)
        )
        self._backoff_initial = (
            cfg.cloud_sync_backoff_initial
            if backoff_initial is None
            else float(backoff_initial)
        )
        self._backoff_max = (
            cfg.cloud_sync_backoff_max if backoff_max is None else float(backoff_max)
        )
        self._base_dir = base_dir

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._attempt = 0
        self._last_cleanup_date: Optional[date] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wake(self) -> None:
        self._wake_event.set()

    def _next_backoff_delay(self, attempt: int) -> float:
        return min(self._backoff_max, self._backoff_initial * (2 ** max(0, attempt)))

    def process_once(self, *, batch_size: int = 50) -> CloudSyncBatchResult:
        """Upload pending outbox items when CLOUD_SYNC=true; no-op when disabled."""
        if not self._settings.cloud_sync:
            return CloudSyncBatchResult(synced=0, failed=0, skipped_disabled=True)

        pending = self._db.get_pending_outbox(limit=batch_size, pending_sync=True)
        if not pending:
            return CloudSyncBatchResult(synced=0, failed=0)

        if not self._client.is_configured:
            logger.warning("CLOUD_SYNC=true but Supabase URL/key is missing; skipping batch.")
            return CloudSyncBatchResult(synced=0, failed=len(pending))

        synced = 0
        for item in pending:
            if self._stop_event.is_set():
                break

            outbox_id = int(item["id"])
            event_id = int(item["event_id"])
            thumb_bytes = load_safe_thumbnail_bytes(
                item.get("snapshot_path"),
                self._settings,
                base_dir=self._base_dir,
            )

            try:
                uploaded_thumb_path: Optional[str] = None
                if thumb_bytes is not None:
                    obj_path = build_thumbnail_object_path(item)
                    uploaded_thumb_path = self._client.upload_thumbnail(obj_path, thumb_bytes)

                payload = build_cloud_event_payload(
                    item,
                    camera_name=self._settings.camera_name,
                    thumbnail_path=uploaded_thumb_path,
                )
                self._client.upsert_event(payload)
            except CloudSyncDeliveryError as exc:
                masked_err = mask_sensitive_text(str(exc), self._settings.secrets())
                self._db.record_outbox_sync_error(outbox_id, masked_err)
                logger.warning(
                    "Cloud sync failed for event id=%d (will retry): %s",
                    event_id,
                    masked_err,
                )
                return CloudSyncBatchResult(synced=synced, failed=1)

            # Confirmed upload succeeded -> only now mark synced=1.
            self._db.mark_outbox_synced(outbox_id)
            synced += 1

        return CloudSyncBatchResult(synced=synced, failed=0)

    def cleanup_if_due(
        self, *, now: Optional[datetime] = None, force: bool = False
    ) -> Optional[CloudCleanupResult]:
        """Run 30-day cloud cleanup once per UTC day after configured hour."""
        if not self._settings.cloud_sync or not self._client.is_configured:
            return None

        utc_now = (
            now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
        )
        if not force:
            if self._last_cleanup_date == utc_now.date():
                return None
            if utc_now.hour < self._settings.cloud_sync_cleanup_hour:
                return None

        result = self._client.cleanup_older_than(
            days=self._settings.cloud_sync_retention_days,
            now=utc_now,
        )
        self._last_cleanup_date = utc_now.date()
        logger.info(
            "Daily cloud cleanup complete: deleted %d rows and %d thumbnails (>%d days).",
            result.rows_deleted,
            result.thumbnails_deleted,
            self._settings.cloud_sync_retention_days,
        )
        return result

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            result = self.process_once()
            if result.failed > 0 and self._settings.cloud_sync:
                delay = self._next_backoff_delay(self._attempt)
                self._attempt += 1
                self._stop_event.wait(delay)
                continue

            self._attempt = 0
            try:
                self.cleanup_if_due()
            except CloudSyncDeliveryError as exc:
                logger.warning(
                    "Daily cloud cleanup failed (will retry): %s",
                    mask_sensitive_text(str(exc), self._settings.secrets()),
                )

            self._wake_event.wait(self._poll_interval)
            self._wake_event.clear()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="cloud-sync-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
