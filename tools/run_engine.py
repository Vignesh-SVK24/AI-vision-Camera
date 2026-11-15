"""Supervised headless engine runner with graceful shutdown, watchdog, and disk guard."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path
from typing import Optional

from config.logging_config import setup_logging
from config.settings import Settings, get_settings
from storage.db import Database
from storage.reliability import (
    DiskSpaceGuard,
    EngineSupervisor,
    FrameWatchdog,
    GracefulShutdown,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def _run_pipeline_worker(
    worker_stop: threading.Event,
    watchdog: FrameWatchdog,
    settings: Settings,
    db: Database,
    disk_guard: DiskSpaceGuard,
) -> int:
    """Run one camera/detection/tracking/face/event cycle until stopped or stalled."""
    from camera.factory import create_camera_source
    from detection.person_detector import PersonDetector
    from events.event_manager import EventManager
    from faces.matcher import FaceMatcher, TrackIdentityCache
    from tracking.tracker import PersonTracker
    from tracking.zones import ZoneMonitor

    event_manager = EventManager(db, settings=settings, disk_guard=disk_guard)
    camera = create_camera_source(settings=settings)

    def _on_camera_event(evt: str, details: dict[str, object]) -> None:
        if evt == "connected":
            event_manager.record_camera_online(source=camera.masked_source)
        elif evt == "disconnected":
            reason = str(details.get("reason", "stream_lost"))
            event_manager.record_camera_offline(reason, source=camera.masked_source)

    camera._on_event = _on_camera_event  # wire callback with camera reference ready
    detector = PersonDetector(settings=settings)
    tracker = PersonTracker(settings=settings)
    zones = ZoneMonitor.from_file()
    matcher = FaceMatcher(db, settings=settings)
    identity_cache = TrackIdentityCache(matcher, settings=settings)

    camera.start()
    logger.info("Engine pipeline worker started (source=%s).", camera.masked_source)
    last_seq = -1
    try:
        while not worker_stop.is_set():
            ok, frame, seq = camera.read_with_seq()
            if not ok or frame is None or seq == last_seq:
                worker_stop.wait(0.05)
                continue

            last_seq = seq
            watchdog.mark_frame()
            det = detector.detect(frame)
            if not det.ran_inference:
                continue

            tracks = tracker.update(frame)
            zone_events = zones.update(tracks)

            for tr in tracks:
                match = identity_cache.identify_track(frame, tr)
                event_manager.record_person_detected(
                    tr.track_id,
                    confidence=tr.confidence,
                    frame=frame,
                    metadata=(
                        {"person_id": match.person_id, "person_name": match.name}
                        if match.matched
                        else None
                    ),
                )
            for ze in zone_events:
                if ze.event_type == "enter":
                    match = identity_cache.get_cached_identity(ze.track_id)
                    event_manager.record_zone_intrusion(
                        ze.zone_id,
                        ze.zone_name,
                        ze.track_id,
                        dwell_seconds=ze.dwell_seconds,
                        frame=frame,
                        metadata=(
                            {"person_id": match.person_id, "person_name": match.name}
                            if match and match.matched
                            else None
                        ),
                    )
        return 0
    finally:
        camera.stop()


def run_supervised_engine(
    *,
    settings: Optional[Settings] = None,
    check_only: bool = False,
) -> int:
    """Start background workers, disk guard, graceful shutdown, and supervised engine."""
    cfg = settings or get_settings()
    setup_logging(cfg)

    shutdown = GracefulShutdown()
    shutdown.install_signal_handlers()

    if check_only:
        logger.info("Engine preflight check passed (app=%s).", cfg.app_name)
        print(f"OK: engine supervisor ready ({cfg.app_name}, stall={cfg.engine_frame_stall_seconds}s)")
        return 0

    from notifications.telegram import OutboxWorker
    from storage.cloud_sync import CloudSyncWorker
    from storage.retention import NightlyRetentionScheduler

    db = Database(db_path=cfg.db_path, settings=cfg)
    shutdown.register(db.close)

    def _on_disk_alert(level: str, message: str) -> None:
        db.insert_event(
            event_type="system_error",
            message=f"disk_{level}: {message}",
        )

    disk_guard = DiskSpaceGuard(
        cfg.snapshots_dir,
        low_warn_mb=cfg.disk_low_warn_mb,
        critical_stop_mb=cfg.disk_critical_stop_mb,
        check_interval_seconds=cfg.disk_check_interval_seconds,
        on_alert=_on_disk_alert,
    )
    disk_guard.check(force=True)

    telegram_worker = OutboxWorker(db, settings=cfg)
    telegram_worker.start()
    shutdown.register(telegram_worker.stop)

    cloud_worker = CloudSyncWorker(db, settings=cfg)
    if cfg.cloud_sync:
        cloud_worker.start()
        shutdown.register(cloud_worker.stop)

    retention = NightlyRetentionScheduler(db, settings=cfg)
    retention.start()
    shutdown.register(retention.stop)

    def _on_stall(elapsed: float) -> None:
        db.insert_event(
            event_type="system_error",
            message=f"Engine frame stall detected ({elapsed:.1f}s); restarting pipeline.",
        )

    supervisor = EngineSupervisor(
        lambda w_stop, watchdog: _run_pipeline_worker(w_stop, watchdog, cfg, db, disk_guard),
        shutdown=shutdown,
        stall_timeout_seconds=cfg.engine_frame_stall_seconds,
        on_stall=_on_stall,
    )

    try:
        return supervisor.run()
    finally:
        shutdown.run_cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run supervised Vision Surveillance engine.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate configuration and exit without opening camera or workers.",
    )
    args = parser.parse_args(argv)
    return run_supervised_engine(check_only=args.check_only)


if __name__ == "__main__":
    raise SystemExit(main())
