"""Live camera preview tool displaying substream, FPS counter, and connection status."""

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

from camera.rtsp_client import RTSPCamera
from config.logging_config import setup_logging
from config.settings import get_settings

logger = logging.getLogger(__name__)

WINDOW_NAME = "Camera Check (press 'q' to quit)"


def _draw_overlay(
    frame: np.ndarray,
    status_text: str,
    connected: bool,
    fps: float,
    source_label: str,
) -> np.ndarray:
    canvas = frame.copy()
    status_color = (0, 220, 0) if connected else (0, 0, 255)

    cv2.rectangle(canvas, (10, 10), (480, 95), (20, 20, 20), thickness=-1)
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
        f"Source: {source_label[:48]}",
        (20, 86),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    return canvas


def _placeholder_frame(width: int = 640, height: int = 360) -> np.ndarray:
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.putText(
        frame,
        "Waiting for video frames...",
        (130, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (180, 180, 180),
        2,
        cv2.LINE_AA,
    )
    return frame


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview live RTSP substream or test video file with FPS and status."
    )
    parser.add_argument(
        "--stream",
        choices=("sub", "main"),
        default="sub",
        help="RTSP stream profile to display (default: sub).",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Optional video file path or RTSP URL override.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)

    status_holder: dict[str, str] = {"label": "CONNECTING"}

    def on_camera_event(event: str, payload: dict[str, Any]) -> None:
        if event == "connected":
            status_holder["label"] = "CONNECTED"
        elif event == "disconnected":
            reason = payload.get("reason", "unknown")
            status_holder["label"] = f"DISCONNECTED ({reason})"
        logger.info("Camera event: %s | %s", event, payload.get("source", ""))

    camera = RTSPCamera(
        settings=settings,
        stream=args.stream,
        source=args.source,
        on_event=on_camera_event,
    )

    display_fps = 0.0
    last_render_time = time.monotonic()

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

            shown_fps = camera.fps if camera.fps > 0 else display_fps
            canvas = frame if ok and frame is not None else _placeholder_frame()
            annotated = _draw_overlay(
                canvas,
                status_text=status_holder["label"],
                connected=camera.is_connected,
                fps=shown_fps,
                source_label=camera.masked_source,
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
