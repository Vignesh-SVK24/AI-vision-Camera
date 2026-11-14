"""Interactive camera calibration and image quality diagnostics tool."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

# Ensure project root is importable when running `python tools/camera_calibrate.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from camera.source import (
    STATUS_CONNECTED,
    CameraSource,
    create_camera_source,
)
from config.logging_config import setup_logging
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)
WINDOW_NAME = "Camera Calibration (s: Snapshot, r: Record 10s, q: Quit)"


def compute_frame_metrics(frame: np.ndarray) -> dict[str, float]:
    """Calculate brightness, contrast, and Laplacian variance sharpness score."""
    if frame is None or frame.size == 0:
        return {"brightness": 0.0, "contrast": 0.0, "sharpness": 0.0}

    if len(frame.shape) == 3 and frame.shape[2] == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame

    brightness = float(np.mean(gray))
    contrast = float(np.std(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    return {
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "sharpness": round(sharpness, 1),
    }


def assess_frame_quality(
    metrics: dict[str, float],
    *,
    min_brightness: float = 40.0,
    max_brightness: float = 215.0,
    min_sharpness: float = 60.0,
) -> list[str]:
    """Return quality warning labels based on brightness and sharpness thresholds."""
    warnings: list[str] = []
    brightness = metrics.get("brightness", 0.0)
    sharpness = metrics.get("sharpness", 0.0)

    if brightness < min_brightness:
        warnings.append("too dark")
    elif brightness > max_brightness:
        warnings.append("too bright")

    if sharpness < min_sharpness:
        warnings.append("blurry")

    return warnings


def save_snapshot(frame: np.ndarray, output_dir: Path) -> Path:
    """Save a full-resolution JPEG snapshot into test_clips/."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"snapshot_{ts}.jpg"
    cv2.imwrite(str(path), frame)
    return path


def record_test_clip(
    source: CameraSource,
    output_dir: Path,
    *,
    duration: float = 10.0,
    target_fps: float = 20.0,
) -> Optional[Path]:
    """Record a 10-second test video clip from the camera source."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    clip_path = output_dir / f"clip_{ts}.mp4"

    # Wait for a valid frame
    frame = None
    for _ in range(30):
        frame = source.get_latest_frame()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        logger.error("Cannot record clip: no frames received from camera.")
        return None

    h, w = frame.shape[:2]
    writer = cv2.VideoWriter(
        str(clip_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        target_fps,
        (w, h),
    )
    if not writer.isOpened():
        logger.error("Failed to initialize VideoWriter for %s", clip_path)
        return None

    interval = 1.0 / target_fps
    deadline = time.monotonic() + duration
    last_seq = -1

    try:
        while time.monotonic() < deadline:
            f, seq = source.get_latest_frame_with_seq()
            if f is not None and seq != last_seq:
                last_seq = seq
                writer.write(f)
            time.sleep(interval * 0.5)
    finally:
        writer.release()

    return clip_path


def _draw_calibration_hud(
    frame: np.ndarray,
    *,
    metrics: dict[str, float],
    warnings: list[str],
    fps: float,
    banner_msg: str = "",
) -> np.ndarray:
    canvas = frame.copy()
    h, w = frame.shape[:2]

    # Draw semi-transparent HUD box at top left
    cv2.rectangle(canvas, (10, 10), (520, 140), (20, 20, 20), thickness=-1)

    # Resolution & FPS
    cv2.putText(
        canvas,
        f"Res: {w}x{h}  |  FPS: {fps:.1f}",
        (20, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    # Brightness & Sharpness score
    cv2.putText(
        canvas,
        f"Brightness: {metrics['brightness']:.1f}  |  Sharpness: {metrics['sharpness']:.1f}",
        (20, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.58,
        (220, 220, 220),
        1,
        cv2.LINE_AA,
    )

    # Quality status and warnings
    if warnings:
        warn_text = "WARNING: " + ", ".join(w.upper() for w in warnings)
        color = (0, 70, 255)  # Orange/Red
    else:
        warn_text = "QUALITY: GOOD"
        color = (0, 230, 0)  # Green

    cv2.putText(
        canvas,
        warn_text,
        (20, 98),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        color,
        2,
        cv2.LINE_AA,
    )

    # Hotkeys
    cv2.putText(
        canvas,
        "[s] Snapshot   [r] Record 10s   [q] Quit",
        (20, 126),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (180, 180, 180),
        1,
        cv2.LINE_AA,
    )

    # Action banner (if any)
    if banner_msg:
        cv2.rectangle(canvas, (10, h - 50), (w - 10, h - 10), (0, 120, 0), thickness=-1)
        cv2.putText(
            canvas,
            banner_msg,
            (25, h - 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    return canvas


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Camera calibration and quality check (sharpness, brightness, warnings, snapshot, clip)."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "test_clips",
        help="Directory to save test snapshots and clips (default: test_clips/).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Seconds to wait for initial camera frame (default: 8.0s).",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional maximum number of frames to process before exiting.",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="Run without opening GUI preview window.",
    )
    return parser.parse_args(argv)


def run_calibration(
    settings: Optional[Settings] = None,
    *,
    source_factory: Optional[Callable[[Settings], CameraSource]] = None,
    output_dir: Optional[Path] = None,
    timeout: float = 8.0,
    max_frames: Optional[int] = None,
    no_preview: bool = False,
    cv2_module: Any = cv2,
) -> int:
    cfg = settings or get_settings()
    out_dir = output_dir or (cfg.log_dir.parent / "test_clips")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("====================================================")
    print(f"  Camera Calibration ({cfg.describe_camera_source()})")
    print(f"  Saving captures to: {out_dir}")
    print("====================================================")

    source = source_factory(cfg) if source_factory is not None else create_camera_source(cfg)
    source.start()

    # Wait for initial frame
    first_frame = None
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        f = source.get_latest_frame()
        if f is not None and source.status == STATUS_CONNECTED:
            first_frame = f
            break
        time.sleep(0.05)

    if first_frame is None:
        source.stop()
        print("\n[FAIL] Could not receive frames from camera.", file=sys.stderr)
        print("Run 'python tools/list_cameras.py' or check Windows camera privacy settings.", file=sys.stderr)
        return 1

    if no_preview:
        metrics = compute_frame_metrics(first_frame)
        warnings = assess_frame_quality(metrics)
        print(f"\nSingle frame check: {first_frame.shape[1]}x{first_frame.shape[0]}")
        print(f"  Brightness: {metrics['brightness']:.1f}  |  Sharpness: {metrics['sharpness']:.1f}")
        print(f"  Status: {'GOOD' if not warnings else ', '.join(warnings)}")
        source.stop()
        return 0

    last_seq = -1
    last_ts = time.perf_counter()
    fps = 0.0
    frame_count = 0
    banner = ""
    banner_expires = 0.0

    try:
        while True:
            frame, seq = source.get_latest_frame_with_seq()
            if frame is not None and seq != last_seq:
                now = time.perf_counter()
                dt = now - last_ts
                last_ts = now
                if last_seq >= 0 and dt > 0:
                    inst_fps = 1.0 / dt
                    fps = inst_fps if fps == 0.0 else (0.85 * fps + 0.15 * inst_fps)
                last_seq = seq
                frame_count += 1

                metrics = compute_frame_metrics(frame)
                warnings = assess_frame_quality(metrics)

                active_banner = banner if time.monotonic() < banner_expires else ""
                display = _draw_calibration_hud(
                    frame,
                    metrics=metrics,
                    warnings=warnings,
                    fps=fps,
                    banner_msg=active_banner,
                )
                cv2_module.imshow(WINDOW_NAME, display)

            if max_frames is not None and frame_count >= max_frames:
                break

            key = cv2_module.waitKey(15) & 0xFF
            if key in (ord("q"), ord("Q")):
                break
            elif key in (ord("s"), ord("S")):
                current = source.get_latest_frame()
                if current is not None:
                    saved = save_snapshot(current, out_dir)
                    banner = f"Snapshot saved: {saved.name}"
                    banner_expires = time.monotonic() + 3.0
                    print(f"[*] Saved test snapshot: {saved}")
            elif key in (ord("r"), ord("R")):
                print("[*] Recording 10-second test clip...")
                clip = record_test_clip(source, out_dir, duration=10.0)
                if clip is not None:
                    banner = f"Clip recorded: {clip.name}"
                    banner_expires = time.monotonic() + 4.0
                    print(f"[*] Saved test clip: {clip}")

            if cv2_module.getWindowProperty(WINDOW_NAME, cv2_module.WND_PROP_VISIBLE) < 1:
                break

        return 0
    finally:
        source.stop()
        cv2_module.destroyAllWindows()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    cfg = get_settings()
    setup_logging(cfg)
    return run_calibration(
        cfg,
        output_dir=args.output_dir,
        timeout=args.timeout,
        max_frames=args.max_frames,
        no_preview=args.no_preview,
    )


if __name__ == "__main__":
    sys.exit(main())
