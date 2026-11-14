"""Restricted polygon zone loading, point-in-polygon checks, and enter/exit state."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

import cv2
import numpy as np

from config.settings import BASE_DIR
from tracking.tracker import TrackedPerson

logger = logging.getLogger(__name__)

DEFAULT_ZONES_PATH = BASE_DIR / "config" / "zones.json"


@dataclass(frozen=True)
class Zone:
    """Configurable polygon zone with dwell-time and enabled controls."""

    zone_id: str
    name: str
    points: list[tuple[int, int]]
    enabled: bool = True
    dwell_seconds: float = 0.0
    color: tuple[int, int, int] = (0, 165, 255)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.zone_id,
            "name": self.name,
            "enabled": self.enabled,
            "dwell_seconds": self.dwell_seconds,
            "points": [[int(x), int(y)] for x, y in self.points],
            "color": [int(c) for c in self.color],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Zone":
        raw_points = data.get("points", [])
        points = [(int(p[0]), int(p[1])) for p in raw_points]
        if len(points) < 3:
            raise ValueError(
                f"Zone '{data.get('id', data.get('name', 'unknown'))}' must have at least 3 points."
            )

        zone_id = str(data.get("id") or data.get("name") or "").strip()
        if not zone_id:
            raise ValueError("Zone entry must include a non-empty 'id' or 'name'.")

        name = str(data.get("name") or zone_id).strip()
        raw_color = data.get("color", [0, 165, 255])
        color = (
            (int(raw_color[0]), int(raw_color[1]), int(raw_color[2]))
            if len(raw_color) >= 3
            else (0, 165, 255)
        )

        return cls(
            zone_id=zone_id,
            name=name,
            points=points,
            enabled=bool(data.get("enabled", True)),
            dwell_seconds=max(0.0, float(data.get("dwell_seconds", 0.0))),
            color=color,
        )


@dataclass(frozen=True)
class ZoneEvent:
    """Zone boundary transition event for a tracked person."""

    event_type: str  # "enter" or "exit"
    zone_id: str
    zone_name: str
    track_id: int
    timestamp: float
    dwell_seconds: float
    anchor_point: tuple[int, int]
    bbox: tuple[int, int, int, int]


@dataclass
class _TrackZoneState:
    entered_at: Optional[float] = None
    last_seen_at: float = 0.0
    is_active: bool = False
    last_anchor: tuple[int, int] = (0, 0)
    last_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)


def bbox_bottom_center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    """Compute bottom-centre (x, y) point of an (x1, y1, x2, y2) bounding box."""
    x1, _, x2, y2 = bbox
    return (int(round((x1 + x2) / 2.0)), int(y2))


def point_in_polygon(
    point: tuple[int, int],
    polygon: Sequence[tuple[int, int]],
) -> bool:
    """Return True if point lies inside or on the boundary of the polygon."""
    if len(polygon) < 3:
        return False
    contour = np.array(polygon, dtype=np.int32)
    result = cv2.pointPolygonTest(contour, (float(point[0]), float(point[1])), False)
    return result >= 0.0


def load_zones(path: Path | str = DEFAULT_ZONES_PATH) -> list[Zone]:
    """Load polygon zones from a JSON file (returns empty list if missing)."""
    target = Path(path)
    if not target.exists():
        logger.warning("Zones file not found at %s; starting with zero zones.", target)
        return []

    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("zones", [])
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError(f"Invalid zones JSON format in {target}")

    return [Zone.from_dict(item) for item in items]


def save_zones(
    zones: Iterable[Zone],
    path: Path | str = DEFAULT_ZONES_PATH,
) -> Path:
    """Persist polygon zones to JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"zones": [z.to_dict() for z in zones]}
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("Saved %d zone(s) to %s", len(payload["zones"]), target)
    return target


class ZoneMonitor:
    """Evaluates tracked person positions against zones with dwell and exit rules."""

    def __init__(
        self,
        zones: Sequence[Zone] = (),
        *,
        exit_grace_seconds: float = 1.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._zones: list[Zone] = list(zones)
        self._exit_grace_seconds = max(0.0, float(exit_grace_seconds))
        self._monotonic = monotonic_fn
        # Keyed by (zone_id, track_id)
        self._states: dict[tuple[str, int], _TrackZoneState] = {}

    @classmethod
    def from_file(
        cls,
        path: Path | str = DEFAULT_ZONES_PATH,
        *,
        exit_grace_seconds: float = 1.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> "ZoneMonitor":
        return cls(
            load_zones(path),
            exit_grace_seconds=exit_grace_seconds,
            monotonic_fn=monotonic_fn,
        )

    @property
    def zones(self) -> list[Zone]:
        return list(self._zones)

    def active_zone_ids(self) -> set[str]:
        """Return IDs of zones currently occupied by at least one active track."""
        return {
            zone_id
            for (zone_id, _), st in self._states.items()
            if st.is_active
        }

    def active_track_ids(self, zone_id: Optional[str] = None) -> set[int]:
        """Return track IDs currently active inside a given zone (or any zone)."""
        return {
            track_id
            for (z_id, track_id), st in self._states.items()
            if st.is_active and (zone_id is None or z_id == zone_id)
        }

    def update(
        self,
        tracks: Iterable[TrackedPerson],
        *,
        timestamp: Optional[float] = None,
    ) -> list[ZoneEvent]:
        """Process current frame tracks and return any enter/exit ZoneEvents."""
        now = self._monotonic() if timestamp is None else float(timestamp)
        track_list = list(tracks)
        visible_ids = {t.track_id for t in track_list}
        events: list[ZoneEvent] = []

        for zone in self._zones:
            if not zone.enabled:
                continue

            for track in track_list:
                key = (zone.zone_id, track.track_id)
                anchor = track.bottom_center
                inside = point_in_polygon(anchor, zone.points)
                state = self._states.get(key)

                if inside:
                    if state is None:
                        state = _TrackZoneState(
                            entered_at=now,
                            last_seen_at=now,
                            is_active=False,
                            last_anchor=anchor,
                            last_bbox=track.bbox,
                        )
                        self._states[key] = state
                    else:
                        if state.entered_at is None:
                            state.entered_at = now
                        state.last_seen_at = now
                        state.last_anchor = anchor
                        state.last_bbox = track.bbox

                    dwell = now - state.entered_at
                    if not state.is_active and dwell >= zone.dwell_seconds:
                        state.is_active = True
                        events.append(
                            ZoneEvent(
                                event_type="enter",
                                zone_id=zone.zone_id,
                                zone_name=zone.name,
                                track_id=track.track_id,
                                timestamp=now,
                                dwell_seconds=dwell,
                                anchor_point=anchor,
                                bbox=track.bbox,
                            )
                        )
                elif state is not None:
                    # Track is visible and outside the zone: immediate exit if active.
                    if state.is_active:
                        dwell = now - (state.entered_at if state.entered_at is not None else now)
                        events.append(
                            ZoneEvent(
                                event_type="exit",
                                zone_id=zone.zone_id,
                                zone_name=zone.name,
                                track_id=track.track_id,
                                timestamp=now,
                                dwell_seconds=dwell,
                                anchor_point=anchor,
                                bbox=track.bbox,
                            )
                        )
                    self._states.pop(key, None)

        # Clean up tracks that disappeared from the frame beyond the exit grace period.
        enabled_zone_map = {z.zone_id: z for z in self._zones if z.enabled}
        for key in list(self._states.keys()):
            zone_id, track_id = key
            if zone_id not in enabled_zone_map:
                self._states.pop(key, None)
                continue
            if track_id in visible_ids:
                continue

            state = self._states[key]
            if (now - state.last_seen_at) > self._exit_grace_seconds:
                if state.is_active:
                    zone = enabled_zone_map[zone_id]
                    dwell = now - (state.entered_at if state.entered_at is not None else now)
                    events.append(
                        ZoneEvent(
                            event_type="exit",
                            zone_id=zone.zone_id,
                            zone_name=zone.name,
                            track_id=track_id,
                            timestamp=now,
                            dwell_seconds=dwell,
                            anchor_point=state.last_anchor,
                            bbox=state.last_bbox,
                        )
                    )
                self._states.pop(key, None)

        return events


def draw_zones(
    frame: np.ndarray,
    zones: Iterable[Zone],
    *,
    active_zone_ids: Optional[set[str]] = None,
    alpha: float = 0.22,
) -> np.ndarray:
    """Draw polygon zones and occupancy status onto a copy of the frame."""
    canvas = frame.copy()
    overlay = frame.copy()
    active_ids = active_zone_ids or set()
    zone_list = list(zones)

    for zone in zone_list:
        if len(zone.points) < 3:
            continue
        pts = np.array(zone.points, dtype=np.int32).reshape((-1, 1, 2))

        if not zone.enabled:
            color = (130, 130, 130)
            status_suffix = " [OFF]"
        elif zone.zone_id in active_ids:
            color = (0, 0, 255)
            status_suffix = " [ALERT]"
        else:
            color = zone.color
            status_suffix = ""

        cv2.fillPoly(overlay, [pts], color)
        cv2.polylines(canvas, [pts], isClosed=True, color=color, thickness=2)

        label_x, label_y = zone.points[0]
        label = f"{zone.name}{status_suffix}"
        cv2.putText(
            canvas,
            label,
            (label_x + 4, max(20, label_y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )

    if zone_list:
        cv2.addWeighted(overlay, alpha, canvas, 1.0 - alpha, 0, dst=canvas)
    return canvas
