"""Runtime reliability primitives: graceful shutdown, frame-stall watchdog, disk guard, and supervisor."""

from __future__ import annotations

import logging
import shutil
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiskStatus:
    state: str  # "ok", "low", "critical"
    free_mb: float
    total_mb: float
    snapshots_allowed: bool


class GracefulShutdown:
    """Coordinates clean signal-driven shutdown and LIFO resource cleanup."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []
        self._cleaned_up = False
        self._signal_reason: Optional[str] = None

    @property
    def is_stopping(self) -> bool:
        return self.stop_event.is_set()

    @property
    def signal_reason(self) -> Optional[str]:
        return self._signal_reason

    def register(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def trigger(self, reason: str = "shutdown requested") -> None:
        if not self.stop_event.is_set():
            self._signal_reason = reason
            logger.info("Initiating graceful shutdown (%s).", reason)
            self.stop_event.set()

    def install_signal_handlers(self) -> None:
        """Register OS handlers for SIGINT, SIGTERM, and Windows SIGBREAK."""
        sig_names = ("SIGINT", "SIGTERM", "SIGBREAK")

        def _handler(signum: int, _frame: object) -> None:
            try:
                name = signal.Signals(signum).name
            except ValueError:
                name = f"signal {signum}"
            self.trigger(name)

        for sig_name in sig_names:
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handler)
                except (ValueError, OSError):
                    # Fails only if called from non-main thread
                    pass

    def run_cleanup(self) -> None:
        with self._lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
            callbacks = list(reversed(self._callbacks))

        for cb in callbacks:
            try:
                cb()
            except Exception as exc:
                logger.warning("Cleanup callback raised exception: %s", exc)


class DiskSpaceGuard:
    """Monitors free disk space, alerts once per incident, and blocks snapshots before disk fills."""

    def __init__(
        self,
        watch_path: Path | str,
        *,
        low_warn_mb: float = 2048.0,
        critical_stop_mb: float = 512.0,
        check_interval_seconds: float = 30.0,
        on_alert: Optional[Callable[[str, str], None]] = None,
        disk_usage_fn: Optional[Callable[[Path], tuple[int, int, int]]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._watch_path = Path(watch_path)
        self._low_warn_mb = max(2.0, float(low_warn_mb))
        self._critical_stop_mb = min(self._low_warn_mb, max(1.0, float(critical_stop_mb)))
        self._check_interval = max(0.0, float(check_interval_seconds))
        self._on_alert = on_alert
        self._disk_usage = disk_usage_fn or (lambda p: shutil.disk_usage(p))
        self._monotonic = monotonic_fn

        self._lock = threading.Lock()
        self._last_checked_at: Optional[float] = None
        self._last_status: Optional[DiskStatus] = None
        self._warned_low = False
        self._warned_critical = False

    def _existing_anchor(self) -> Path:
        current = self._watch_path.resolve() if not self._watch_path.exists() else self._watch_path
        while not current.exists() and current.parent != current:
            current = current.parent
        return current

    def check(self, *, force: bool = False) -> DiskStatus:
        now = self._monotonic()
        with self._lock:
            if (
                not force
                and self._last_checked_at is not None
                and self._last_status is not None
                and (now - self._last_checked_at) < self._check_interval
            ):
                return self._last_status

            anchor = self._existing_anchor()
            total_b, _used_b, free_b = self._disk_usage(anchor)
            free_mb = free_b / (1024.0 * 1024.0)
            total_mb = total_b / (1024.0 * 1024.0)

            alert_to_send: Optional[tuple[str, str]] = None
            if free_mb < self._critical_stop_mb:
                state = "critical"
                snapshots_allowed = False
                if not self._warned_critical:
                    self._warned_critical = True
                    self._warned_low = True
                    alert_to_send = (
                        "critical",
                        (
                            f"Critical low disk space ({free_mb:.1f} MB free < "
                            f"{self._critical_stop_mb:.0f} MB): snapshot saving paused."
                        ),
                    )
            elif free_mb < self._low_warn_mb:
                state = "low"
                snapshots_allowed = True
                if not self._warned_low:
                    self._warned_low = True
                    alert_to_send = (
                        "low",
                        (
                            f"Low disk space warning ({free_mb:.1f} MB free < "
                            f"{self._low_warn_mb:.0f} MB)."
                        ),
                    )
            else:
                state = "ok"
                snapshots_allowed = True
                self._warned_low = False
                self._warned_critical = False

            status = DiskStatus(
                state=state,
                free_mb=free_mb,
                total_mb=total_mb,
                snapshots_allowed=snapshots_allowed,
            )
            self._last_checked_at = now
            self._last_status = status

        if alert_to_send is not None:
            level, msg = alert_to_send
            logger.error("DiskSpaceGuard [%s]: %s", level.upper(), msg)
            if self._on_alert is not None:
                try:
                    self._on_alert(level, msg)
                except Exception as exc:
                    logger.warning("Disk alert callback failed: %s", exc)

        return status

    def can_save_snapshot(self, *, force_check: bool = False) -> bool:
        return self.check(force=force_check).snapshots_allowed


class FrameWatchdog:
    """Detects frame-processing stalls and triggers restart/recovery callbacks."""

    def __init__(
        self,
        *,
        stall_timeout_seconds: float = 30.0,
        on_stall: Optional[Callable[[float], None]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stall_timeout = max(0.05, float(stall_timeout_seconds))
        self._on_stall = on_stall
        self._monotonic = monotonic_fn
        self._lock = threading.Lock()
        self._last_frame_at = self._monotonic()
        self._stalled = False

    def reset(self) -> None:
        with self._lock:
            self._last_frame_at = self._monotonic()
            self._stalled = False

    def mark_frame(self) -> None:
        with self._lock:
            self._last_frame_at = self._monotonic()
            self._stalled = False

    def elapsed_since_frame(self) -> float:
        with self._lock:
            return max(0.0, self._monotonic() - self._last_frame_at)

    def check(self) -> bool:
        """Return True if healthy, False if stalled (fires on_stall once per stall)."""
        fire_elapsed: Optional[float] = None
        with self._lock:
            elapsed = self._monotonic() - self._last_frame_at
            if elapsed > self._stall_timeout:
                if not self._stalled:
                    self._stalled = True
                    fire_elapsed = elapsed
                is_healthy = False
            else:
                is_healthy = True

        if fire_elapsed is not None:
            logger.error(
                "FrameWatchdog detected frame stall (%.2fs > %.2fs).",
                fire_elapsed,
                self._stall_timeout,
            )
            if self._on_stall is not None:
                self._on_stall(fire_elapsed)

        return is_healthy


class EngineSupervisor:
    """Restarts an engine worker if frames stall or it exits unexpectedly."""

    def __init__(
        self,
        worker_fn: Callable[[threading.Event, FrameWatchdog], int],
        *,
        shutdown: Optional[GracefulShutdown] = None,
        stall_timeout_seconds: float = 30.0,
        check_interval_seconds: float = 1.0,
        restart_backoff_initial: float = 1.0,
        restart_backoff_max: float = 30.0,
        max_restarts: Optional[int] = None,
        on_stall: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._worker_fn = worker_fn
        self._shutdown = shutdown or GracefulShutdown()
        self._stall_timeout = max(0.05, float(stall_timeout_seconds))
        self._check_interval = max(0.01, float(check_interval_seconds))
        self._backoff_initial = max(0.01, float(restart_backoff_initial))
        self._backoff_max = max(self._backoff_initial, float(restart_backoff_max))
        self._max_restarts = max_restarts
        self._on_stall = on_stall
        self.restarts = 0
        self.stall_restarts = 0

    def run(self) -> int:
        attempt = 0
        while not self._shutdown.is_stopping:
            worker_stop = threading.Event()
            stall_triggered = threading.Event()

            def _handle_stall(elapsed: float) -> None:
                stall_triggered.set()
                worker_stop.set()
                if self._on_stall is not None:
                    self._on_stall(elapsed)

            watchdog = FrameWatchdog(
                stall_timeout_seconds=self._stall_timeout,
                on_stall=_handle_stall,
            )

            exit_holder: dict[str, Optional[int | BaseException]] = {"code": None, "err": None}

            def _runner() -> None:
                try:
                    exit_holder["code"] = self._worker_fn(worker_stop, watchdog)
                except BaseException as exc:  # noqa: BLE001
                    exit_holder["err"] = exc
                    exit_holder["code"] = 1

            thread = threading.Thread(target=_runner, name="supervised-engine", daemon=True)
            thread.start()

            while thread.is_alive():
                if self._shutdown.stop_event.wait(self._check_interval):
                    worker_stop.set()
                    break
                watchdog.check()

            thread.join(timeout=5.0)
            if self._shutdown.is_stopping:
                return 0

            if stall_triggered.is_set():
                self.stall_restarts += 1
                logger.warning("Restarting engine worker after frame stall (restarts=%d).", self.restarts + 1)
            elif exit_holder["err"] is not None:
                logger.exception(
                    "Engine worker crashed with exception: %s", exit_holder["err"]
                )
            elif exit_holder["code"] == 0:
                return 0
            else:
                logger.warning(
                    "Engine worker exited unexpectedly (code=%s); restarting.",
                    exit_holder["code"],
                )

            self.restarts += 1
            if self._max_restarts is not None and self.restarts > self._max_restarts:
                logger.error("Exceeded maximum engine restarts (%d); stopping.", self._max_restarts)
                return 1

            delay = min(self._backoff_max, self._backoff_initial * (2 ** min(attempt, 6)))
            attempt += 1
            if self._shutdown.stop_event.wait(delay):
                return 0

        return 0
