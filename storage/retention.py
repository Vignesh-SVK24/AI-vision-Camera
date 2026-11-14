"""Nightly retention job for purging expired events and JPEG snapshots."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config.settings import BASE_DIR, Settings, get_settings
from storage.db import Database
from storage.snapshots import delete_snapshot_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionReport:
    """Summary of items removed during a retention cleanup run."""

    cutoff_iso: str
    deleted_events: int
    deleted_outbox: int
    deleted_snapshots: int
    missing_snapshots: int
    removed_empty_dirs: int


def _prune_empty_date_dirs(snapshots_dir: Optional[Path]) -> int:
    if snapshots_dir is None or not snapshots_dir.exists():
        return 0
    removed = 0
    for child in sorted(snapshots_dir.iterdir(), reverse=True):
        if child.is_dir():
            try:
                if not any(child.iterdir()):
                    child.rmdir()
                    removed += 1
            except OSError:
                pass
    return removed


def run_retention(
    db: Database,
    *,
    retention_days: int = 30,
    snapshots_dir: Optional[Path | str] = None,
    base_dir: Path = BASE_DIR,
    now: Optional[datetime] = None,
) -> RetentionReport:
    """Delete events and snapshot files older than retention_days and log results."""
    current = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max(1, int(retention_days)))

    deleted_events, deleted_outbox, snapshot_paths = db.delete_events_before(cutoff.timestamp())

    deleted_snapshots = 0
    missing_snapshots = 0
    for stored_path in snapshot_paths:
        try:
            if delete_snapshot_file(stored_path, base_dir=base_dir):
                deleted_snapshots += 1
            else:
                missing_snapshots += 1
        except Exception as exc:
            logger.warning("Failed to delete snapshot '%s': %s", stored_path, exc)

    resolved_snapshots_dir = Path(snapshots_dir) if snapshots_dir is not None else None
    removed_dirs = _prune_empty_date_dirs(resolved_snapshots_dir)

    report = RetentionReport(
        cutoff_iso=cutoff.isoformat(),
        deleted_events=deleted_events,
        deleted_outbox=deleted_outbox,
        deleted_snapshots=deleted_snapshots,
        missing_snapshots=missing_snapshots,
        removed_empty_dirs=removed_dirs,
    )

    if deleted_events > 0 or deleted_snapshots > 0:
        db.record_audit(
            actor="retention_job",
            action="purge_expired_data",
            target_type="events",
            details={
                "cutoff_iso": report.cutoff_iso,
                "retention_days": retention_days,
                "deleted_events": report.deleted_events,
                "deleted_outbox": report.deleted_outbox,
                "deleted_snapshots": report.deleted_snapshots,
                "missing_snapshots": report.missing_snapshots,
                "removed_empty_dirs": report.removed_empty_dirs,
            },
        )

    logger.info(
        "Retention cleanup complete (cutoff=%s): removed %d event(s), %d outbox row(s), "
        "%d snapshot file(s) (%d missing), %d empty folder(s).",
        report.cutoff_iso,
        report.deleted_events,
        report.deleted_outbox,
        report.deleted_snapshots,
        report.missing_snapshots,
        report.removed_empty_dirs,
    )
    return report


def seconds_until_next_run(
    run_hour: int,
    run_minute: int,
    *,
    now: Optional[datetime] = None,
) -> float:
    """Compute seconds from now until the next local occurrence of run_hour:run_minute."""
    current = now if now is not None else datetime.now().astimezone()
    target = current.replace(
        hour=run_hour,
        minute=run_minute,
        second=0,
        microsecond=0,
    )
    if target <= current:
        target = target + timedelta(days=1)
    return max(1.0, (target - current).total_seconds())


class NightlyRetentionScheduler:
    """Daemon scheduler that executes the retention cleanup once every night."""

    def __init__(
        self,
        db: Database,
        settings: Optional[Settings] = None,
        *,
        retention_days: Optional[int] = None,
        run_hour: Optional[int] = None,
        run_minute: Optional[int] = None,
        snapshots_dir: Optional[Path | str] = None,
    ) -> None:
        cfg = settings or get_settings()
        self._db = db
        self._retention_days = (
            cfg.retention_days if retention_days is None else int(retention_days)
        )
        self._run_hour = cfg.retention_run_hour if run_hour is None else int(run_hour)
        self._run_minute = (
            cfg.retention_run_minute if run_minute is None else int(run_minute)
        )
        self._snapshots_dir = (
            Path(snapshots_dir) if snapshots_dir is not None else cfg.snapshots_dir
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_now(self, *, now: Optional[datetime] = None) -> RetentionReport:
        """Execute retention immediately (useful on startup or in tests)."""
        return run_retention(
            self._db,
            retention_days=self._retention_days,
            snapshots_dir=self._snapshots_dir,
            now=now,
        )

    def start(self) -> "NightlyRetentionScheduler":
        if self.is_running:
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="nightly-retention",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Nightly retention scheduler started (daily at %02d:%02d, retention=%dd).",
            self._run_hour,
            self._run_minute,
            self._retention_days,
        )
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("Nightly retention scheduler stopped.")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            delay = seconds_until_next_run(self._run_hour, self._run_minute)
            if self._stop_event.wait(delay):
                break
            try:
                self.run_now()
            except Exception:
                logger.exception("Nightly retention job failed.")
