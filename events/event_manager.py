"""Event lifecycle manager with per-track/per-zone cooldowns and snapshot capture."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np

from config.logging_config import mask_sensitive_text
from config.settings import BASE_DIR, Settings, get_settings
from storage.db import Database
from storage.snapshots import format_snapshot_path, save_snapshot
from tracking.tracker import TrackedPerson
from tracking.zones import ZoneEvent

logger = logging.getLogger(__name__)

EVENT_PERSON_DETECTED = "person_detected"
EVENT_ZONE_INTRUSION = "zone_intrusion"
EVENT_CAMERA_OFFLINE = "camera_offline"
EVENT_CAMERA_ONLINE = "camera_online"
EVENT_SYSTEM_ERROR = "system_error"

SUPPORTED_EVENT_TYPES = frozenset(
    {
        EVENT_PERSON_DETECTED,
        EVENT_ZONE_INTRUSION,
        EVENT_CAMERA_OFFLINE,
        EVENT_CAMERA_ONLINE,
        EVENT_SYSTEM_ERROR,
    }
)


class EventManager:
    """Deduplicates continuous-presence / cooldown spam and persists events + outbox."""

    def __init__(
        self,
        db: Database,
        settings: Optional[Settings] = None,
        *,
        track_cooldown_seconds: Optional[float] = None,
        zone_cooldown_seconds: Optional[float] = None,
        absence_reset_seconds: Optional[float] = None,
        snapshots_dir: Optional[Path | str] = None,
        snapshot_max_width: Optional[int] = None,
        snapshot_quality: Optional[int] = None,
        disk_guard: Optional[Any] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        cfg = settings or get_settings()
        self._db = db
        self._secrets = cfg.secrets()
        self._track_cooldown_seconds = max(
            0.0,
            cfg.event_track_cooldown_seconds
            if track_cooldown_seconds is None
            else float(track_cooldown_seconds),
        )
        self._zone_cooldown_seconds = max(
            0.0,
            cfg.event_zone_cooldown_seconds
            if zone_cooldown_seconds is None
            else float(zone_cooldown_seconds),
        )
        self._absence_reset_seconds = max(
            0.0,
            cfg.event_absence_reset_seconds
            if absence_reset_seconds is None
            else float(absence_reset_seconds),
        )
        self._snapshots_dir = (
            Path(snapshots_dir) if snapshots_dir is not None else cfg.snapshots_dir
        )
        self._snapshot_max_width = (
            cfg.snapshot_max_width
            if snapshot_max_width is None
            else int(snapshot_max_width)
        )
        self._snapshot_quality = (
            cfg.snapshot_quality
            if snapshot_quality is None
            else int(snapshot_quality)
        )
        self._disk_guard = disk_guard
        self._monotonic = monotonic_fn

        self._lock = threading.Lock()
        self._last_track_event_at: dict[int, float] = {}
        self._last_zone_event_at: dict[tuple[str, Optional[int]], float] = {}
        # Tracks currently considered continuously in view: track_id -> last_seen_monotonic
        self._continuous_tracks_seen: dict[int, float] = {}
        self._continuous_tracks_emitted: set[int] = set()
        self._camera_online_state: Optional[bool] = None

    def _save_optional_snapshot(
        self,
        frame: Optional[np.ndarray],
        *,
        prefix: str,
        created_at: Optional[datetime] = None,
    ) -> Optional[str]:
        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        if self._disk_guard is not None and not self._disk_guard.can_save_snapshot():
            logger.warning("Skipping snapshot write (%s): disk space critical.", prefix)
            return None
        path = save_snapshot(
            frame,
            self._snapshots_dir,
            prefix=prefix,
            max_width=self._snapshot_max_width,
            quality=self._snapshot_quality,
            timestamp=created_at,
        )
        return format_snapshot_path(path, base_dir=BASE_DIR)

    def _now_mono(self, timestamp: Optional[float]) -> float:
        return self._monotonic() if timestamp is None else float(timestamp)

    def record_person_detected(
        self,
        track_id: int,
        *,
        confidence: Optional[float] = None,
        frame: Optional[np.ndarray] = None,
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Record a person_detected event respecting the per-track cooldown."""
        now = self._now_mono(timestamp)
        with self._lock:
            last_at = self._last_track_event_at.get(track_id)
            if last_at is not None and (now - last_at) < self._track_cooldown_seconds:
                return None
            self._last_track_event_at[track_id] = now

        snapshot_path = self._save_optional_snapshot(
            frame,
            prefix=f"person_{track_id}",
            created_at=created_at,
        )
        event_id, _ = self._db.insert_event(
            event_type=EVENT_PERSON_DETECTED,
            track_id=track_id,
            confidence=confidence,
            snapshot_path=snapshot_path,
            message=f"Person detected (track #{track_id})",
            metadata=metadata,
            created_at=created_at,
        )
        logger.info("Recorded %s event id=%d (track=#%d)", EVENT_PERSON_DETECTED, event_id, track_id)
        return event_id

    def record_zone_intrusion(
        self,
        zone_id: str,
        zone_name: str,
        track_id: Optional[int],
        *,
        confidence: Optional[float] = None,
        dwell_seconds: float = 0.0,
        frame: Optional[np.ndarray] = None,
        metadata: Optional[dict[str, Any]] = None,
        timestamp: Optional[float] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Record a zone_intrusion event respecting the per-zone+track cooldown."""
        now = self._now_mono(timestamp)
        key = (zone_id, track_id)
        with self._lock:
            last_at = self._last_zone_event_at.get(key)
            if last_at is not None and (now - last_at) < self._zone_cooldown_seconds:
                return None
            self._last_zone_event_at[key] = now

        snapshot_path = self._save_optional_snapshot(
            frame,
            prefix=f"zone_{zone_id}_{track_id if track_id is not None else 'na'}",
            created_at=created_at,
        )
        merged_meta = dict(metadata) if metadata else {}
        merged_meta["dwell_seconds"] = round(dwell_seconds, 3)

        event_id, _ = self._db.insert_event(
            event_type=EVENT_ZONE_INTRUSION,
            track_id=track_id,
            zone_id=zone_id,
            zone_name=zone_name,
            confidence=confidence,
            snapshot_path=snapshot_path,
            message=f"Zone intrusion in '{zone_name}' (track #{track_id})",
            metadata=merged_meta,
            created_at=created_at,
        )
        logger.info(
            "Recorded %s event id=%d (zone=%s, track=#%s)",
            EVENT_ZONE_INTRUSION,
            event_id,
            zone_id,
            track_id,
        )
        return event_id

    def record_camera_offline(
        self,
        reason: str = "disconnected",
        *,
        source: str = "",
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Record a camera_offline transition (deduplicated while already offline)."""
        with self._lock:
            if self._camera_online_state is False:
                return None
            self._camera_online_state = False

        safe_reason = mask_sensitive_text(reason, self._secrets)
        safe_source = mask_sensitive_text(source, self._secrets)
        merged_meta = dict(metadata) if metadata else {}
        if safe_source:
            merged_meta["source"] = safe_source
        merged_meta["reason"] = safe_reason

        event_id, _ = self._db.insert_event(
            event_type=EVENT_CAMERA_OFFLINE,
            message=f"Camera offline: {safe_reason}",
            metadata=merged_meta,
            created_at=created_at,
        )
        logger.warning("Recorded %s event id=%d (%s)", EVENT_CAMERA_OFFLINE, event_id, safe_reason)
        return event_id

    def record_camera_online(
        self,
        *,
        source: str = "",
        metadata: Optional[dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> Optional[int]:
        """Record a camera_online transition (deduplicated while already online)."""
        with self._lock:
            if self._camera_online_state is True:
                return None
            self._camera_online_state = True

        safe_source = mask_sensitive_text(source, self._secrets)
        merged_meta = dict(metadata) if metadata else {}
        if safe_source:
            merged_meta["source"] = safe_source

        event_id, _ = self._db.insert_event(
            event_type=EVENT_CAMERA_ONLINE,
            message="Camera online",
            metadata=merged_meta,
            created_at=created_at,
        )
        logger.info("Recorded %s event id=%d", EVENT_CAMERA_ONLINE, event_id)
        return event_id

    def record_system_error(
        self,
        message: str,
        *,
        component: str = "system",
        details: Optional[dict[str, Any]] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        """Persist a system_error event with sensitive values masked."""
        safe_msg = mask_sensitive_text(message, self._secrets)
        merged_meta = {"component": component}
        if details:
            for k, v in details.items():
                merged_meta[k] = (
                    mask_sensitive_text(v, self._secrets)
                    if isinstance(v, str)
                    else v
                )

        event_id, _ = self._db.insert_event(
            event_type=EVENT_SYSTEM_ERROR,
            message=safe_msg,
            metadata=merged_meta,
            created_at=created_at,
        )
        logger.error("Recorded %s event id=%d (%s)", EVENT_SYSTEM_ERROR, event_id, safe_msg)
        return event_id

    def process_tracks(
        self,
        tracks: Iterable[TrackedPerson],
        *,
        zone_events: Iterable[ZoneEvent] = (),
        frame: Optional[np.ndarray] = None,
        timestamp: Optional[float] = None,
        created_at: Optional[datetime] = None,
    ) -> list[int]:
        """Evaluate current frame tracks and zone events, preventing continuous-person spam."""
        now = self._now_mono(timestamp)
        track_list = list(tracks)
        track_by_id = {t.track_id: t for t in track_list}
        created_ids: list[int] = []

        # Expire continuous-presence state only if a track has been absent > absence_reset_seconds.
        with self._lock:
            for tid in list(self._continuous_tracks_seen.keys()):
                if tid not in track_by_id:
                    if (now - self._continuous_tracks_seen[tid]) > self._absence_reset_seconds:
                        self._continuous_tracks_seen.pop(tid, None)
                        self._continuous_tracks_emitted.discard(tid)

            tracks_to_emit: list[TrackedPerson] = []
            for track in track_list:
                self._continuous_tracks_seen[track.track_id] = now
                if track.track_id not in self._continuous_tracks_emitted:
                    self._continuous_tracks_emitted.add(track.track_id)
                    tracks_to_emit.append(track)

        for track in tracks_to_emit:
            event_id = self.record_person_detected(
                track.track_id,
                confidence=track.confidence,
                frame=frame,
                metadata={"bbox": list(track.bbox)},
                timestamp=now,
                created_at=created_at,
            )
            if event_id is not None:
                created_ids.append(event_id)

        for z_ev in zone_events:
            if z_ev.event_type != "enter":
                continue
            matched_track = track_by_id.get(z_ev.track_id)
            conf = matched_track.confidence if matched_track else None
            event_id = self.record_zone_intrusion(
                z_ev.zone_id,
                z_ev.zone_name,
                z_ev.track_id,
                confidence=conf,
                dwell_seconds=z_ev.dwell_seconds,
                frame=frame,
                metadata={
                    "anchor_point": list(z_ev.anchor_point),
                    "bbox": list(z_ev.bbox),
                },
                timestamp=now,
                created_at=created_at,
            )
            if event_id is not None:
                created_ids.append(event_id)

        return created_ids
