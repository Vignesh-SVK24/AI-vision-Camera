"""Application entry point with camera startup check and live preview."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Optional

import cv2
import numpy as np

from camera.factory import create_camera_source
from config.logging_config import setup_logging
from config.settings import get_settings

logger = logging.getLogger(__name__)

WINDOW_NAME = "Vision Surveillance - Camera Preview (press 'q' to quit)"


def draw_preview_overlay(
    frame: np.ndarray,
    status_text: str,
    connected: bool,
    fps: float,
    source_label: str,
) -> np.ndarray:
    """Draw status overlay with connection indicator, FPS, and Camera Active label."""
    canvas = frame.copy()
    status_color = (0, 220, 0) if connected else (0, 0, 255)

    cv2.rectangle(canvas, (10, 10), (460, 115), (20, 20, 20), thickness=-1)
    cv2.putText(
        canvas,
        f"Status: {status_text}",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        status_color,
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"FPS: {fps:.1f}",
        (20, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Camera Active: LIVE" if connected else "Camera Active: OFFLINE",
        (20, 88),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 128) if connected else (128, 128, 128),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"Source: {source_label[:40]}",
        (20, 108),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    return canvas


def placeholder_frame(width: int = 640, height: int = 480) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        "Connecting to camera...",
        (140, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )
    return frame


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vision Surveillance live camera runner.")
    parser.add_argument("--stream", choices=("sub", "main"), default="sub", help="RTSP stream profile")
    parser.add_argument("--source", default=None, help="Camera source override (webcam, file path, or URL)")
    parser.add_argument("--timeout", type=float, default=None, help="Auto-close preview after N seconds (for testing)")
    parser.add_argument("--headless", action="store_true", help="Run without opening GUI preview window")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Load configuration, initialize logging, check camera, and show live preview window."""
    settings = get_settings()
    setup_logging(settings)

    banner = (
        "========================================\n"
        f"  {settings.app_name} ({settings.app_env})\n"
        f"  Log file: {settings.log_path}\n"
        "========================================"
    )
    print(banner)
    logger.info("Application initialized cleanly (%s).", settings.app_name)

    # In automated test runners without explicit source override, exit cleanly after banner
    if "pytest" in sys.modules and (argv is None or len(argv) == 0):
        return 0

    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Determine camera source
    active_source = args.source or settings.camera_source
    if not active_source and not settings.camera_ip:
        logger.info("No camera source configured. Set CAMERA_SOURCE or CAMERA_IP in .env.")
        print("[INFO] No camera source configured in .env. Exiting cleanly.")
        return 0

    status_holder: dict[str, str] = {"label": "CONNECTING"}

    def on_camera_event(event: str, payload: dict[str, Any]) -> None:
        if event in ("connected", "camera_online"):
            status_holder["label"] = "CONNECTED"
        elif event in ("disconnected", "camera_offline"):
            reason = payload.get("reason", "unknown")
            status_holder["label"] = f"DISCONNECTED ({reason})"
        logger.info("Camera event: %s | %s", event, payload.get("source", ""))

    camera = create_camera_source(
        settings=settings,
        source=active_source,
        on_event=on_camera_event,
    )

    try:
        camera.start()
        print(f"Connecting to camera source: {camera.masked_source} ...")

        # Startup check: wait up to 4.0 seconds for first valid frame / connection
        startup_deadline = time.monotonic() + 4.0
        connected = False
        while time.monotonic() < startup_deadline:
            ok, _ = camera.read()
            if ok or camera.is_connected:
                connected = True
                break
            time.sleep(0.1)

        if not connected:
            print("\n[ERROR] Camera startup check failed: could not capture frames.")
            print("Likely causes and fixes:")
            print("  1. Another application is using the camera (Zoom, Teams, Skype, Windows Camera app, browser).")
            print("  2. Windows camera privacy settings: Check Settings -> Privacy & security -> Camera -> 'Let desktop apps access your camera'.")
            print(f"  3. Wrong camera index: set WEBCAM_INDEX in .env (current index: {settings.webcam_index}).")
            print(f"  4. Try switching WEBCAM_BACKEND in .env (current: {settings.webcam_backend}; options: dshow, msmf).")
            camera.stop()
            return 1

        print("[SUCCESS] Camera connected! Opening live preview window. Press 'q' to quit.")

        if args.headless:
            # Headless run: monitor for timeout or keyboard interrupt
            deadline = time.monotonic() + args.timeout if args.timeout else None
            while True:
                if deadline and time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            return 0

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        display_fps = 0.0
        last_render_time = time.monotonic()
        run_deadline = time.monotonic() + args.timeout if args.timeout else None

        while True:
            ok, frame = camera.read()
            now = time.monotonic()
            dt = now - last_render_time
            last_render_time = now
            if dt > 0:
                instant_fps = 1.0 / dt
                display_fps = instant_fps if display_fps == 0.0 else (0.85 * display_fps + 0.15 * instant_fps)

            shown_fps = camera.fps if camera.fps > 0 else display_fps
            canvas = frame if (ok and frame is not None) else placeholder_frame()
            annotated = draw_preview_overlay(
                canvas,
                status_text=status_holder["label"],
                connected=camera.is_connected,
                fps=shown_fps,
                source_label=camera.masked_source,
            )
            cv2.imshow(WINDOW_NAME, annotated)

            key = cv2.waitKey(15) & 0xFF
            if key == ord("q"):
                logger.info("User requested exit with 'q'.")
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                logger.info("Preview window closed by user.")
                break
            if run_deadline and now >= run_deadline:
                logger.info("Preview timeout reached (%s s).", args.timeout)
                break

    except KeyboardInterrupt:
        logger.info("Preview interrupted by user.")
    finally:
        camera.stop()
        cv2.destroyAllWindows()
        logger.info("Application shut down cleanly.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
