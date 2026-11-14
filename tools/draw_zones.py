"""Interactive tool to draw polygon zones on a snapshot and preview zone tracking."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running `python tools/draw_zones.py`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np

from camera.rtsp_client import RTSPCamera
from config.logging_config import setup_logging
from config.settings import Settings, get_settings
from tracking.tracker import PersonTracker, draw_tracks
from tracking.zones import (
    DEFAULT_ZONES_PATH,
    Zone,
    ZoneMonitor,
    draw_zones,
    load_zones,
    save_zones,
)

logger = logging.getLogger(__name__)

DRAW_WINDOW = "Draw Restricted Zones (L-Click: add | R-Click/u: undo | n: finish | s: save | q: quit)"
PREVIEW_WINDOW = "Zone & Track Preview (press 'q' to quit)"


def _grab_snapshot(
    settings: Settings,
    *,
    image_path: Optional[str] = None,
    source: Optional[str] = None,
    stream: str = "sub",
    timeout: float = 10.0,
) -> np.ndarray:
    if image_path:
        img = cv2.imread(image_path)
        if img is None:
            raise RuntimeError(f"Could not read image file: {image_path}")
        return img

    camera = RTSPCamera(settings=settings, stream=stream, source=source)
    deadline = time.monotonic() + timeout
    with camera:
        while time.monotonic() < deadline:
            ok, frame = camera.read()
            if ok and frame is not None:
                return frame
            time.sleep(0.05)

    raise RuntimeError(
        f"Timed out after {timeout:.1f}s waiting for a snapshot from {camera.masked_source}."
    )


def _render_editor(
    base_image: np.ndarray,
    saved_zones: list[Zone],
    current_points: list[tuple[int, int]],
    mouse_pos: Optional[tuple[int, int]],
) -> np.ndarray:
    canvas = draw_zones(base_image, saved_zones)

    for idx, pt in enumerate(current_points):
        cv2.circle(canvas, pt, 5, (0, 255, 255), thickness=-1)
        if idx > 0:
            cv2.line(canvas, current_points[idx - 1], pt, (0, 255, 255), 2)

    if len(current_points) >= 1 and mouse_pos is not None:
        cv2.line(canvas, current_points[-1], mouse_pos, (200, 200, 0), 1)
    if len(current_points) >= 3:
        cv2.line(canvas, current_points[-1], current_points[0], (0, 200, 200), 1)

    cv2.rectangle(canvas, (10, 10), (540, 62), (20, 20, 20), thickness=-1)
    cv2.putText(
        canvas,
        f"Saved zones: {len(saved_zones)} | Current points: {len(current_points)}",
        (20, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "L-Click: point | R-Click/u: undo | n: finish polygon | d: del last | s: save | q: quit",
        (20, 54),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (200, 200, 200),
        1,
        cv2.LINE_AA,
    )
    return canvas


def _prompt_zone_metadata(index: int) -> tuple[str, str, float, bool]:
    default_name = f"Zone {index}"
    try:
        raw_name = input(f"Zone name [{default_name}]: ").strip()
        name = raw_name or default_name
        raw_dwell = input("Dwell threshold in seconds [0.0]: ").strip()
        dwell = max(0.0, float(raw_dwell)) if raw_dwell else 0.0
        raw_enabled = input("Enabled? [Y/n]: ").strip().lower()
        enabled = raw_enabled not in ("n", "no", "0", "false")
    except (EOFError, ValueError):
        name = default_name
        dwell = 0.0
        enabled = True

    zone_id = "_".join(name.lower().split())
    return zone_id, name, dwell, enabled


def run_zone_editor(
    snapshot: np.ndarray,
    zones_path: Path,
) -> int:
    zones = load_zones(zones_path)
    current_points: list[tuple[int, int]] = []
    mouse_pos: list[Optional[tuple[int, int]]] = [None]

    def on_mouse(event: int, x: int, y: int, flags: int, param: object) -> None:
        mouse_pos[0] = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            current_points.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and current_points:
            current_points.pop()

    cv2.namedWindow(DRAW_WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(DRAW_WINDOW, on_mouse)

    try:
        while True:
            canvas = _render_editor(snapshot, zones, current_points, mouse_pos[0])
            cv2.imshow(DRAW_WINDOW, canvas)

            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27):  # q or Esc
                break
            if key in (ord("u"), 8):  # u or Backspace
                if current_points:
                    current_points.pop()
            elif key in (ord("n"), 13, 32):  # n, Enter, or Space
                if len(current_points) < 3:
                    print("Need at least 3 points to create a polygon zone.")
                    continue
                zone_id, name, dwell, enabled = _prompt_zone_metadata(len(zones) + 1)
                existing_ids = {z.zone_id for z in zones}
                while zone_id in existing_ids:
                    zone_id = f"{zone_id}_{len(zones) + 1}"
                zones.append(
                    Zone(
                        zone_id=zone_id,
                        name=name,
                        points=list(current_points),
                        enabled=enabled,
                        dwell_seconds=dwell,
                    )
                )
                current_points.clear()
                print(f"Added zone '{name}' (id={zone_id}, dwell={dwell:.1f}s, enabled={enabled}).")
            elif key == ord("d"):
                if zones:
                    removed = zones.pop()
                    print(f"Removed last zone: {removed.name}")
            elif key == ord("s"):
                save_zones(zones, zones_path)
                print(f"Saved {len(zones)} zone(s) to {zones_path}")

            if cv2.getWindowProperty(DRAW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cv2.destroyAllWindows()

    return 0


def run_zone_preview(
    settings: Settings,
    *,
    zones_path: Path,
    source: Optional[str],
    stream: str,
) -> int:
    zones = load_zones(zones_path)
    monitor = ZoneMonitor(zones)
    tracker = PersonTracker(settings=settings)
    camera = RTSPCamera(settings=settings, stream=stream, source=source)

    cv2.namedWindow(PREVIEW_WINDOW, cv2.WINDOW_NORMAL)
    last_seq = 0
    recent_banner = ""

    try:
        with camera:
            while True:
                ok, frame, seq = camera.read_with_seq()
                if not ok or frame is None or seq == last_seq:
                    if (cv2.waitKey(10) & 0xFF) == ord("q"):
                        break
                    continue

                last_seq = seq
                tracks = tracker.update(frame)
                events = monitor.update(tracks)

                for ev in events:
                    msg = (
                        f"{ev.event_type.upper()} zone='{ev.zone_name}' "
                        f"track=#{ev.track_id} dwell={ev.dwell_seconds:.1f}s"
                    )
                    logger.info("Zone event: %s", msg)
                    print(msg)
                    recent_banner = msg

                canvas = draw_zones(
                    frame,
                    monitor.zones,
                    active_zone_ids=monitor.active_zone_ids(),
                )
                canvas = draw_tracks(canvas, tracks)

                if recent_banner:
                    cv2.putText(
                        canvas,
                        recent_banner,
                        (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )

                cv2.imshow(PREVIEW_WINDOW, canvas)
                if (cv2.waitKey(1) & 0xFF) == ord("q"):
                    break
                if cv2.getWindowProperty(PREVIEW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
    except KeyboardInterrupt:
        logger.info("Zone preview interrupted by user.")
    finally:
        cv2.destroyAllWindows()

    return 0


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Draw restricted polygon zones on a snapshot or preview live zone tracking."
    )
    parser.add_argument(
        "--zones-file",
        default=str(DEFAULT_ZONES_PATH),
        help="Path to zones JSON file (default: config/zones.json).",
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Optional static image path to draw zones onto.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Optional video file path or RTSP URL override.",
    )
    parser.add_argument(
        "--stream",
        choices=("sub", "main"),
        default="sub",
        help="RTSP stream profile when capturing from live camera (default: sub).",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Run live ByteTrack + zone monitoring preview instead of the zone editor.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings)
    zones_path = Path(args.zones_file)

    if args.preview:
        return run_zone_preview(
            settings,
            zones_path=zones_path,
            source=args.source,
            stream=args.stream,
        )

    snapshot = _grab_snapshot(
        settings,
        image_path=args.image,
        source=args.source,
        stream=args.stream,
    )
    return run_zone_editor(snapshot, zones_path)


if __name__ == "__main__":
    sys.exit(main())
