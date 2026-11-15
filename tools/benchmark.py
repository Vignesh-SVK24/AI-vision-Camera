"""Benchmark person detection latency, effective FPS, and CPU usage on video or live stream."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Ensure project root is importable when running `python tools/benchmark.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from camera.factory import create_camera_source
from config.logging_config import setup_logging
from config.settings import Settings, get_settings
from detection.person_detector import PersonDetector, draw_detections

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None  # type: ignore

logger = logging.getLogger(__name__)

WINDOW_NAME = "Person Detection Benchmark (press 'q' to quit)"


@dataclass(frozen=True)
class BenchmarkSummary:
    """Aggregated performance metrics from a detection benchmark run."""

    label: str
    source: str
    backend: str
    wall_seconds: float
    frames_read: int
    checks_run: int
    inferences_run: int
    motion_skipped: int
    avg_inference_ms: float
    min_inference_ms: float
    max_inference_ms: float
    effective_fps: float
    process_cpu_percent: float
    system_cpu_percent: Optional[float]


def _draw_hud(
    frame: np.ndarray,
    *,
    backend: str,
    inf_ms: float,
    eff_fps: float,
    person_count: int,
    motion_active: bool,
    motion_gate_enabled: bool,
) -> np.ndarray:
    canvas = frame.copy()
    cv2.rectangle(canvas, (10, 10), (430, 118), (20, 20, 20), thickness=-1)
    motion_label = (
        ("ACTIVE" if motion_active else "IDLE (YOLO gated)")
        if motion_gate_enabled
        else "DISABLED"
    )
    motion_color = (0, 220, 0) if motion_active or not motion_gate_enabled else (0, 200, 255)

    cv2.putText(
        canvas,
        f"Backend: {backend} | People: {person_count}",
        (20, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"Inf: {inf_ms:.1f} ms | Eff FPS: {eff_fps:.1f}",
        (20, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"Motion Gate: {motion_label}",
        (20, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        motion_color,
        1,
        cv2.LINE_AA,
    )
    return canvas


def _run_loop(
    *,
    label: str,
    source_label: str,
    detector: PersonDetector,
    read_frame_fn,
    duration: Optional[float],
    max_frames: Optional[int],
    preview: bool,
) -> BenchmarkSummary:
    if psutil is not None:
        psutil.cpu_percent(interval=None)

    cpu_count = os.cpu_count() or 1
    wall_start = time.perf_counter()
    proc_cpu_start = time.process_time()

    frames_read = 0
    checks_run = 0
    motion_skipped = 0
    inference_times: list[float] = []
    last_inf_ms = 0.0
    last_motion = False

    if preview:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    try:
        while True:
            if duration is not None and (time.perf_counter() - wall_start) >= duration:
                break
            if max_frames is not None and frames_read >= max_frames:
                break

            ok, frame, eof = read_frame_fn()
            if eof:
                break
            if not ok or frame is None:
                if preview and (cv2.waitKey(10) & 0xFF) == ord("q"):
                    break
                time.sleep(0.01)
                continue

            frames_read += 1
            result = detector.detect(frame)

            if not result.skipped:
                checks_run += 1
                last_motion = result.motion_detected
                if result.ran_inference:
                    inference_times.append(result.inference_ms)
                    last_inf_ms = result.inference_ms
                elif detector.motion_gate_enabled and not result.motion_detected:
                    motion_skipped += 1

            elapsed = max(1e-6, time.perf_counter() - wall_start)
            eff_fps = frames_read / elapsed

            if preview:
                annotated = draw_detections(frame, result.detections)
                annotated = _draw_hud(
                    annotated,
                    backend=detector.backend,
                    inf_ms=last_inf_ms,
                    eff_fps=eff_fps,
                    person_count=len(result.detections),
                    motion_active=last_motion,
                    motion_gate_enabled=detector.motion_gate_enabled,
                )
                cv2.imshow(WINDOW_NAME, annotated)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    break
    except KeyboardInterrupt:
        logger.info("Benchmark interrupted by user.")
    finally:
        if preview:
            cv2.destroyAllWindows()

    wall_elapsed = max(1e-6, time.perf_counter() - wall_start)
    proc_cpu_elapsed = max(0.0, time.process_time() - proc_cpu_start)
    process_cpu_pct = min(100.0, (proc_cpu_elapsed / wall_elapsed) * 100.0 / cpu_count)
    system_cpu_pct = float(psutil.cpu_percent(interval=None)) if psutil is not None else None

    avg_ms = sum(inference_times) / len(inference_times) if inference_times else 0.0
    min_ms = min(inference_times) if inference_times else 0.0
    max_ms = max(inference_times) if inference_times else 0.0
    eff_fps = frames_read / wall_elapsed

    return BenchmarkSummary(
        label=label,
        source=source_label,
        backend=detector.backend,
        wall_seconds=wall_elapsed,
        frames_read=frames_read,
        checks_run=checks_run,
        inferences_run=len(inference_times),
        motion_skipped=motion_skipped,
        avg_inference_ms=avg_ms,
        min_inference_ms=min_ms,
        max_inference_ms=max_ms,
        effective_fps=eff_fps,
        process_cpu_percent=process_cpu_pct,
        system_cpu_percent=system_cpu_pct,
    )


def benchmark_video_file(
    video_path: str | Path,
    detector: PersonDetector,
    *,
    duration: Optional[float] = None,
    max_frames: Optional[int] = None,
    preview: bool = True,
) -> BenchmarkSummary:
    """Benchmark person detection on a local video file."""
    path_str = str(video_path)
    cap = cv2.VideoCapture(path_str)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {path_str}")

    native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if native_fps <= 1.0 or native_fps > 120.0:
        native_fps = 30.0
    frame_interval = 1.0 / native_fps
    next_due = time.perf_counter()

    def _read_video() -> tuple[bool, Optional[np.ndarray], bool]:
        nonlocal next_due
        ok, frame = cap.read()
        if not ok or frame is None:
            return False, None, True
        now = time.perf_counter()
        sleep_for = next_due - now
        if sleep_for > 0:
            time.sleep(sleep_for)
        next_due = max(next_due + frame_interval, time.perf_counter())
        return True, frame, False

    try:
        return _run_loop(
            label="Video File",
            source_label=path_str,
            detector=detector,
            read_frame_fn=_read_video,
            duration=duration,
            max_frames=max_frames,
            preview=preview,
        )
    finally:
        cap.release()


def benchmark_live_stream(
    settings: Settings,
    detector: PersonDetector,
    *,
    stream: str = "sub",
    duration: float = 15.0,
    max_frames: Optional[int] = None,
    preview: bool = True,
) -> BenchmarkSummary:
    """Benchmark person detection on the configured live RTSP stream."""
    camera = create_camera_source(settings=settings)
    last_seq = 0

    def _read_live() -> tuple[bool, Optional[np.ndarray], bool]:
        nonlocal last_seq
        ok, frame, seq = camera.read_with_seq()
        if not ok or frame is None or seq == last_seq:
            return False, None, False
        last_seq = seq
        return True, frame, False

    with camera:
        return _run_loop(
            label=f"Live Stream ({stream})",
            source_label=camera.masked_source,
            detector=detector,
            read_frame_fn=_read_live,
            duration=duration,
            max_frames=max_frames,
            preview=preview,
        )


def print_report(summary: BenchmarkSummary, detector: PersonDetector) -> None:
    """Print a structured benchmark summary to stdout."""
    print("\n================ BENCHMARK REPORT ================")
    print(f"Target            : {summary.label}")
    print(f"Source            : {summary.source}")
    print(f"Backend           : {summary.backend}")
    print(
        f"Model / Config    : {detector.model_path.name} | "
        f"imgsz={detector.imgsz} | conf={detector.confidence:.2f} | "
        f"target_fps={detector.target_fps:.1f} | motion_gate={detector.motion_gate_enabled}"
    )
    print(f"Wall Time         : {summary.wall_seconds:.2f} s")
    print(f"Frames Read       : {summary.frames_read}")
    print(
        f"Checks / YOLO Run : {summary.checks_run} checks | "
        f"{summary.inferences_run} YOLO | {summary.motion_skipped} idle-skipped"
    )
    print(
        f"Inference Time    : avg={summary.avg_inference_ms:.2f} ms "
        f"(min={summary.min_inference_ms:.2f} ms, max={summary.max_inference_ms:.2f} ms)"
    )
    print(f"Effective FPS     : {summary.effective_fps:.2f} FPS")
    cpu_line = f"{summary.process_cpu_percent:.1f}% process"
    if summary.system_cpu_percent is not None:
        cpu_line += f" | {summary.system_cpu_percent:.1f}% system"
    print(f"CPU Usage         : {cpu_line}")
    print("==================================================\n")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark YOLO person detection on a video file and/or live RTSP stream."
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Path to a local video file to benchmark.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also (or only) benchmark the live RTSP stream from .env.",
    )
    parser.add_argument(
        "--stream",
        choices=("sub", "main"),
        default="sub",
        help="RTSP stream profile when using --live (default: sub).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Maximum seconds to run per benchmark target (default: full video, 15s for live).",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional maximum number of frames per benchmark target.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=None,
        help="Override detection confidence threshold (e.g. 0.45).",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="Override YOLO input size (e.g. 640 or 416).",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=None,
        help="Override target detector FPS (e.g. 4.0).",
    )
    parser.add_argument(
        "--no-openvino",
        action="store_true",
        help="Disable OpenVINO export and use PyTorch CPU inference directly.",
    )
    parser.add_argument(
        "--no-motion-gate",
        action="store_true",
        help="Disable the MOG2 motion gate (runs YOLO at target FPS on every check).",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Run headless without opening an OpenCV preview window.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    detector = PersonDetector(
        settings=settings,
        confidence=args.conf,
        imgsz=args.imgsz,
        target_fps=args.target_fps,
        use_openvino=False if args.no_openvino else None,
        motion_gate_enabled=False if args.no_motion_gate else None,
    )

    preview = not args.no_preview
    run_video = args.source is not None
    run_live = args.live or (not run_video)

    if run_video:
        summary = benchmark_video_file(
            args.source,
            detector,
            duration=args.duration,
            max_frames=args.max_frames,
            preview=preview,
        )
        print_report(summary, detector)

    if run_live:
        live_duration = 15.0 if args.duration is None else args.duration
        summary = benchmark_live_stream(
            settings,
            detector,
            stream=args.stream,
            duration=live_duration,
            max_frames=args.max_frames,
            preview=preview,
        )
        print_report(summary, detector)

    return 0


if __name__ == "__main__":
    sys.exit(main())
