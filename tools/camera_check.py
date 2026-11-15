"""Live camera preview and diagnosis tool for any camera source (webcam, URL/RTSP, or file)."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure project root is importable when running `python tools/camera_check.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from camera.factory import create_camera_source
from config.logging_config import setup_logging
from config.settings import get_settings

logger = logging.getLogger(__name__)

WINDOW_NAME = "Camera Check & Diagnosis (press 'q' to quit)"


def _draw_overlay(
    frame: np.ndarray,
    status_text: str,
    connected: bool,
    fps: float,
    source_label: str,
    resolution: str,
) -> np.ndarray:
    canvas = frame.copy()
    status_color = (0, 220, 0) if connected else (0, 0, 255)

    cv2.rectangle(canvas, (10, 10), (500, 115), (20, 20, 20), thickness=-1)
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
        f"FPS: {fps:.1f} | Res: {resolution}",
        (20, 62),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        f"Source: {source_label[:46]}",
        (20, 88),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "Press 'q' in this window to exit",
        (20, 108),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (160, 160, 160),
        1,
        cv2.LINE_AA,
    )
    return canvas


def _placeholder_frame(width: int = 640, height: int = 480) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        "Connecting to camera source...",
        (90, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )
    return frame


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose and preview any camera source (webcam, RTSP/HTTP URL, or file)."
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Camera source override (e.g. 'webcam', 'rtsp://...', 'test_clips/sample.mp4').",
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Webcam device index override (default: from .env or 0).",
    )
    parser.add_argument(
        "--backend",
        choices=("dshow", "msmf"),
        default=None,
        help="Webcam backend override ('dshow' or 'msmf').",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    status_holder: dict[str, str] = {"label": "CONNECTING"}

    def on_camera_event(event: str, payload: dict[str, Any]) -> None:
        if event == "camera_online":
            status_holder["label"] = "CONNECTED"
        elif event == "camera_offline":
            status_holder["label"] = "DISCONNECTED"
        logger.info("Camera event: %s | %s", event, payload.get("source", ""))

    camera = create_camera_source(
        settings=settings,
        source=args.source,
        index=args.index,
        backend=args.backend,
        on_event=on_camera_event,
    )

    display_fps = 0.0
    last_render_time = time.monotonic()
    resolution_str = "Unknown"

    try:
        camera.start()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

        while True:
            ok, frame = camera.read()
            now = time.monotonic()
            dt = now - last_render_time
            last_render_time = now
            if dt > 0:
                instant_fps = 1.0 / dt
                display_fps = (
                    instant_fps
                    if display_fps == 0.0
                    else (0.85 * display_fps + 0.15 * instant_fps)
                )

            if ok and frame is not None:
                resolution_str = f"{frame.shape[1]}x{frame.shape[0]}"
                canvas = frame
            else:
                canvas = _placeholder_frame()

            shown_fps = camera.fps if camera.fps > 0 else display_fps
            annotated = _draw_overlay(
                canvas,
                status_text=camera.status.value.upper(),
                connected=camera.is_connected,
                fps=shown_fps,
                source_label=camera.masked_source,
                resolution=resolution_str,
            )
            cv2.imshow(WINDOW_NAME, annotated)

            key = cv2.waitKey(15) & 0xFF
            if key == ord("q"):
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        logger.info("Camera check interrupted by user.")
    finally:
        camera.stop()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    sys.exit(main())
