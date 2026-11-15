"""Camera discovery and hardware diagnostic tool scanning DirectShow and MSMF on Windows."""

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


def probe_camera(
    index: int,
    backend_name: str,
    *,
    capture_factory: Optional[Callable[[int, int], Any]] = None,
    warmup_seconds: float = 3.0,
) -> Optional[dict[str, Any]]:
    """Test a specific camera index and backend; return properties or None if unopenable."""
    factory = capture_factory or cv2.VideoCapture
    flag = getattr(cv2, f"CAP_{backend_name.upper()}", 0)
    try:
        cap = factory(index, flag) if flag else factory(index)
    except Exception:
        return None

    if cap is None or not cap.isOpened():
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None

    frame_ok = False
    width = 0
    height = 0
    t_start = time.perf_counter()
    deadline = time.monotonic() + warmup_seconds
    frames_read = 0

    while time.monotonic() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None and frame.size > 0:
            frame_ok = True
            frames_read += 1
            height, width = frame.shape[:2]
            if frames_read >= 5:
                break
        time.sleep(0.04)

    t_elapsed = time.perf_counter() - t_start
    fps = round(frames_read / max(0.001, t_elapsed), 1)

    try:
        cap.release()
    except Exception:
        pass

    return {
        "index": index,
        "backend": backend_name,
        "opened": True,
        "frame_ok": frame_ok,
        "width": width,
        "height": height,
        "fps": fps,
    }


def scan_cameras(
    max_index: int = 4,
    backends: tuple[str, ...] = ("dshow", "msmf"),
) -> list[dict[str, Any]]:
    """Scan all camera indexes across specified backends."""
    results: list[dict[str, Any]] = []
    print(f"Scanning camera indexes 0..{max_index} on backends {backends}...")
    for idx in range(max_index + 1):
        for b_name in backends:
            res = probe_camera(idx, b_name)
            if res is not None:
                results.append(res)
    return results


def print_table(results: list[dict[str, Any]]) -> None:
    """Format and display discovered cameras."""
    print("=" * 70)
    print(f"{'Index':<7} | {'Backend':<8} | {'Opened':<8} | {'Frame OK':<9} | {'Resolution':<12} | {'FPS':<6}")
    print("-" * 70)
    if not results:
        print("  No cameras could be opened. Verify camera privacy settings and USB connections.")
    for r in results:
        res_str = f"{r['width']}x{r['height']}" if r["frame_ok"] else "N/A"
        print(
            f"{r['index']:<7} | {r['backend']:<8} | {str(r['opened']):<8} | "
            f"{str(r['frame_ok']):<9} | {res_str:<12} | {r['fps']:<6.1f}"
        )
    print("=" * 70)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scan and report available webcam devices on Windows.")
    parser.add_argument("--max-index", type=int, default=3, help="Highest index to probe (default: 3).")
    parser.add_argument(
        "--backend",
        choices=("dshow", "msmf", "all"),
        default="all",
        help="Backend to probe (default: all).",
    )
    args = parser.parse_args(argv)

    backends = ("dshow", "msmf") if args.backend == "all" else (args.backend,)
    results = scan_cameras(max_index=args.max_index, backends=backends)
    print_table(results)

    working = [r for r in results if r["frame_ok"]]
    if working:
        best = working[0]
        print(f"\n[RECOMMENDATION] Set in your .env file:")
        print(f"CAMERA_SOURCE=webcam")
        print(f"WEBCAM_INDEX={best['index']}")
        print(f"WEBCAM_BACKEND={best['backend']}")
    else:
        print("\n[TROUBLESHOOTING] No working cameras detected. Check Windows Settings -> Privacy -> Camera.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
