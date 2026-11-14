"""Multi-day soak test logger recording CPU, memory, event counts, and memory drift."""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_process_rss_mb() -> float:
    """Return current process RSS / WorkingSet in MB (psutil if available, else Windows API)."""
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)
    except ImportError:
        pass

    if os.name == "nt":
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if ok:
                return counters.WorkingSetSize / (1024.0 * 1024.0)
        except Exception:
            pass
    return 0.0


@dataclass(frozen=True)
class SoakSummary:
    samples: int
    start_rss_mb: float
    end_rss_mb: float
    max_rss_mb: float
    rss_growth_mb: float
    output_csv: Path


def run_soak_test(
    *,
    db_path: Path,
    output_csv: Path,
    duration_seconds: float,
    interval_seconds: float = 60.0,
    max_iterations: Optional[int] = None,
    simulate_events: bool = False,
) -> SoakSummary:
    """Log CPU %, process RSS MB, SQLite event counts, and free disk MB over time."""
    from storage.db import Database

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    prev_wall = time.monotonic()
    prev_cpu = sum(os.times()[:2])
    start_wall = prev_wall
    cpu_count = max(1, os.cpu_count() or 1)

    start_rss: Optional[float] = None
    max_rss = 0.0
    last_rss = 0.0
    samples = 0

    with Database(db_path=db_path) as db, output_csv.open(
        "w", newline="", encoding="utf-8"
    ) as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "timestamp_utc",
                "elapsed_seconds",
                "cpu_percent",
                "rss_mb",
                "rss_delta_mb",
                "events_total",
                "outbox_pending_notify",
                "outbox_pending_sync",
                "free_disk_mb",
            ]
        )

        while True:
            if max_iterations is not None and samples >= max_iterations:
                break
            now_wall = time.monotonic()
            elapsed = now_wall - start_wall
            if samples > 0 and elapsed >= duration_seconds:
                break

            if simulate_events:
                db.insert_event(
                    event_type="person_detected",
                    track_id=(samples % 5) + 1,
                    confidence=0.88,
                    message=f"Soak synthetic sample #{samples}",
                )

            now_cpu = sum(os.times()[:2])
            dt_wall = max(1e-6, now_wall - prev_wall)
            cpu_pct = (
                0.0
                if samples == 0
                else min(100.0, max(0.0, ((now_cpu - prev_cpu) / (dt_wall * cpu_count)) * 100.0))
            )
            prev_wall = now_wall
            prev_cpu = now_cpu

            rss_mb = get_process_rss_mb()
            if start_rss is None:
                start_rss = rss_mb
            max_rss = max(max_rss, rss_mb)
            last_rss = rss_mb
            rss_delta = rss_mb - start_rss

            total_events = int(db.get_latest_camera_status()["total_events"])
            pending_notify = len(db.get_pending_outbox(limit=1000, pending_notify=True))
            pending_sync = len(db.get_pending_outbox(limit=1000, pending_sync=True))
            free_mb = shutil.disk_usage(db_path.parent).free / (1024.0 * 1024.0)

            writer.writerow(
                [
                    datetime.now(timezone.utc).isoformat(),
                    f"{elapsed:.2f}",
                    f"{cpu_pct:.2f}",
                    f"{rss_mb:.2f}",
                    f"{rss_delta:.2f}",
                    total_events,
                    pending_notify,
                    pending_sync,
                    f"{free_mb:.1f}",
                ]
            )
            fh.flush()
            samples += 1

            if max_iterations is not None and samples >= max_iterations:
                break
            remaining = duration_seconds - (time.monotonic() - start_wall)
            if remaining <= 0:
                break
            time.sleep(min(interval_seconds, remaining))

    baseline = start_rss or 0.0
    return SoakSummary(
        samples=samples,
        start_rss_mb=baseline,
        end_rss_mb=last_rss,
        max_rss_mb=max_rss,
        rss_growth_mb=last_rss - baseline,
        output_csv=output_csv,
    )


def main(argv: list[str] | None = None) -> int:
    from config.settings import get_settings

    cfg = get_settings()
    parser = argparse.ArgumentParser(
        description="Log CPU, memory, and SQLite event counts over a multi-day soak test."
    )
    parser.add_argument("--days", type=float, default=7.0, help="Duration in days (default: 7)")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="Override total duration in seconds (useful for 24h=86400 or quick smoke runs)",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=60.0,
        help="Sampling interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Optional fixed sample count cap",
    )
    parser.add_argument(
        "--db",
        default=str(cfg.db_path),
        help="SQLite DB path to monitor (default: DB_PATH from .env)",
    )
    parser.add_argument(
        "--output",
        default=str(cfg.log_dir / "soak_metrics.csv"),
        help="CSV output file path",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Generate synthetic events each sample to stress-test storage",
    )
    parser.add_argument(
        "--max-rss-growth-mb",
        type=float,
        default=None,
        help="Fail with exit code 2 if RSS grows by more than this many MB",
    )
    args = parser.parse_args(argv)

    total_seconds = (
        float(args.duration_seconds)
        if args.duration_seconds is not None
        else max(1.0, float(args.days) * 86400.0)
    )
    summary = run_soak_test(
        db_path=Path(args.db),
        output_csv=Path(args.output),
        duration_seconds=total_seconds,
        interval_seconds=max(0.01, float(args.interval_seconds)),
        max_iterations=args.iterations,
        simulate_events=bool(args.simulate),
    )
    print(
        f"Soak complete: samples={summary.samples}, start_rss={summary.start_rss_mb:.2f} MB, "
        f"end_rss={summary.end_rss_mb:.2f} MB, max_rss={summary.max_rss_mb:.2f} MB, "
        f"growth={summary.rss_growth_mb:+.2f} MB, csv={summary.output_csv}"
    )
    if (
        args.max_rss_growth_mb is not None
        and summary.rss_growth_mb > float(args.max_rss_growth_mb)
    ):
        print(
            f"FAIL: RSS growth {summary.rss_growth_mb:.2f} MB exceeded threshold "
            f"{args.max_rss_growth_mb:.2f} MB.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
