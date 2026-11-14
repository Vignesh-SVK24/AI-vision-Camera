"""Camera index discovery tool scanning DirectShow cameras on Windows."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

# Ensure project root is importable when running `python tools/list_cameras.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np


def scan_single_camera(
    index: int,
    *,
    capture_factory: Optional[Callable[[int, int], Any]] = None,
    warmup_frames: int = 2,
    measure_frames: int = 10,
    read_timeout: float = 2.0,
) -> Optional[dict[str, Any]]:
    """Probe a single camera index via DirectShow; return specs or None if unopenable."""
    factory = capture_factory or cv2.VideoCapture
    cap = factory(index, cv2.CAP_DSHOW)
    if cap is None or not cap.isOpened():
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None

    try:
        # Warm-up frames
        for _ in range(warmup_frames):
            ok, frame = cap.read()
            if not ok or frame is None:
                return None

        # Measure FPS and resolution
        t_start = time.perf_counter()
        frames_read = 0
        last_frame: Optional[np.ndarray] = None
        deadline = time.monotonic() + max(0.5, float(read_timeout))

        while frames_read < measure_frames and time.monotonic() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                frames_read += 1
                last_frame = frame

        duration = time.perf_counter() - t_start
        measured_fps = (frames_read / duration) if duration > 0 and frames_read > 0 else 0.0

        prop_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        prop_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_h, actual_w = last_frame.shape[:2] if last_frame is not None else (prop_h, prop_w)

        return {
            "index": index,
            "width": actual_w or prop_w,
            "height": actual_h or prop_h,
            "fps": round(measured_fps, 1),
        }
    finally:
        try:
            cap.release()
        except Exception:
            pass


def scan_cameras(
    min_index: int = 0,
    max_index: int = 5,
    *,
    capture_factory: Optional[Callable[[int, int], Any]] = None,
) -> list[dict[str, Any]]:
    """Scan camera indexes from min_index to max_index using cv2.CAP_DSHOW."""
    found: list[dict[str, Any]] = []
    for idx in range(min_index, max_index + 1):
        info = scan_single_camera(idx, capture_factory=capture_factory)
        if info is not None:
            found.append(info)
    return found


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan camera indexes 0 to 5 via DirectShow and report working cameras."
    )
    parser.add_argument(
        "--min-index",
        type=int,
        default=0,
        help="Start index to scan (default: 0).",
    )
    parser.add_argument(
        "--max-index",
        type=int,
        default=5,
        help="End index to scan (default: 5).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    print("====================================================")
    print(f"  Scanning DirectShow cameras (indexes {args.min_index} to {args.max_index})...")
    print("====================================================")

    working = scan_cameras(args.min_index, args.max_index)

    if not working:
        print("\n[!] No working cameras found on indexes 0 to 5.", file=sys.stderr)
        print("\nTroubleshooting fixes on Windows:", file=sys.stderr)
        print("  1. Close any application using the camera (Zoom, Teams, OBS, Windows Camera app, browser tabs).", file=sys.stderr)
        print("  2. Open Windows Settings -> Privacy & security -> Camera and turn ON 'Camera access' and 'Let desktop apps access your camera'.", file=sys.stderr)
        print("  3. Check Device Manager -> Cameras / Imaging devices to ensure your camera is enabled.", file=sys.stderr)
        return 1

    print(f"\nFound {len(working)} working camera(s):\n")
    for cam in working:
        print(f"  * Camera Index {cam['index']}: {cam['width']}x{cam['height']} @ {cam['fps']} FPS (DirectShow)")

    recommended = working[0]["index"]
    print("\n----------------------------------------------------")
    print(f"RECOMMENDATION: Set this in your .env file:")
    print(f"  CAMERA_SOURCE=webcam")
    print(f"  WEBCAM_INDEX={recommended}")
    print("----------------------------------------------------")
    return 0


if __name__ == "__main__":
    sys.exit(main())
