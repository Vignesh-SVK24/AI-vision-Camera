"""Unit tests for ByteTrack PersonTracker, polygon zones, dwell, enter/exit, and drawing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from config.settings import Settings
from tracking.tracker import PersonTracker, TrackedPerson, draw_tracks
from tracking.zones import (
    Zone,
    ZoneMonitor,
    bbox_bottom_center,
    draw_zones,
    load_zones,
    point_in_polygon,
    save_zones,
)


def _square_zone(
    zone_id: str = "restricted",
    *,
    enabled: bool = True,
    dwell_seconds: float = 0.0,
) -> Zone:
    return Zone(
        zone_id=zone_id,
        name="Restricted Area",
        points=[(100, 100), (200, 100), (200, 200), (100, 200)],
        enabled=enabled,
        dwell_seconds=dwell_seconds,
    )


def _person(track_id: int, bottom_x: int, bottom_y: int, conf: float = 0.9) -> TrackedPerson:
    return TrackedPerson(
        track_id=track_id,
        bbox=(bottom_x - 20, bottom_y - 80, bottom_x + 20, bottom_y),
        confidence=conf,
    )


def test_bottom_center_and_point_in_polygon() -> None:
    bbox = (80, 100, 120, 180)
    assert bbox_bottom_center(bbox) == (100, 180)

    poly = [(100, 100), (200, 100), (200, 200), (100, 200)]
    assert point_in_polygon((150, 150), poly) is True
    assert point_in_polygon((100, 100), poly) is True  # boundary included
    assert point_in_polygon((50, 150), poly) is False
    assert point_in_polygon((150, 210), poly) is False
    assert point_in_polygon((150, 150), [(0, 0), (10, 10)]) is False


def test_zones_json_round_trip(tmp_path: Path) -> None:
    zones_file = tmp_path / "zones.json"
    zones = [
        _square_zone("gate_a", enabled=True, dwell_seconds=1.5),
        _square_zone("gate_b", enabled=False, dwell_seconds=0.0),
    ]
    save_zones(zones, zones_file)
    loaded = load_zones(zones_file)

    assert len(loaded) == 2
    assert loaded[0].zone_id == "gate_a"
    assert loaded[0].enabled is True
    assert loaded[0].dwell_seconds == 1.5
    assert loaded[0].points == [(100, 100), (200, 100), (200, 200), (100, 200)]
    assert loaded[1].enabled is False


def test_zone_enter_once_and_exit_once_on_walk_through() -> None:
    monitor = ZoneMonitor([_square_zone(dwell_seconds=0.0)], exit_grace_seconds=0.5)

    # Frame 1: outside zone -> no events
    assert monitor.update([_person(1, 50, 150)], timestamp=1.0) == []

    # Frame 2: walks inside -> exactly one enter event
    ev_enter = monitor.update([_person(1, 150, 150)], timestamp=2.0)
    assert len(ev_enter) == 1
    assert ev_enter[0].event_type == "enter"
    assert ev_enter[0].track_id == 1
    assert ev_enter[0].zone_id == "restricted"

    # Frames 3-4: stays inside -> no duplicate enter events
    assert monitor.update([_person(1, 160, 160)], timestamp=3.0) == []
    assert monitor.update([_person(1, 170, 170)], timestamp=4.0) == []
    assert monitor.active_zone_ids() == {"restricted"}

    # Frame 5: walks out -> exactly one exit event
    ev_exit = monitor.update([_person(1, 250, 150)], timestamp=5.0)
    assert len(ev_exit) == 1
    assert ev_exit[0].event_type == "exit"
    assert ev_exit[0].track_id == 1
    assert ev_exit[0].dwell_seconds == 3.0
    assert monitor.active_zone_ids() == set()

    # Frame 6: remains outside -> no extra exit event
    assert monitor.update([_person(1, 270, 150)], timestamp=6.0) == []


def test_zone_dwell_threshold_prevents_brief_passthrough_and_fires_after_dwell() -> None:
    monitor = ZoneMonitor([_square_zone(dwell_seconds=2.0)], exit_grace_seconds=0.5)

    # Track 1 enters briefly for 1.0s (< 2.0s) and leaves -> no events at all
    assert monitor.update([_person(1, 150, 150)], timestamp=10.0) == []
    assert monitor.update([_person(1, 155, 150)], timestamp=11.0) == []
    assert monitor.update([_person(1, 250, 150)], timestamp=11.5) == []

    # Track 2 enters at t=20.0, stays until t=22.0 (>= 2.0s) -> enter fires at t=22.0
    assert monitor.update([_person(2, 150, 150)], timestamp=20.0) == []
    assert monitor.update([_person(2, 150, 150)], timestamp=21.5) == []
    ev_enter = monitor.update([_person(2, 150, 150)], timestamp=22.0)
    assert len(ev_enter) == 1
    assert ev_enter[0].event_type == "enter"
    assert ev_enter[0].track_id == 2
    assert ev_enter[0].dwell_seconds >= 2.0

    # Leaves at t=24.0 -> one exit event
    ev_exit = monitor.update([_person(2, 50, 150)], timestamp=24.0)
    assert len(ev_exit) == 1
    assert ev_exit[0].event_type == "exit"


def test_disabled_zone_emits_no_events_and_grace_handles_disappearance() -> None:
    monitor = ZoneMonitor(
        [
            _square_zone("disabled_zone", enabled=False, dwell_seconds=0.0),
            _square_zone("active_zone", enabled=True, dwell_seconds=0.0),
        ],
        exit_grace_seconds=1.0,
    )

    ev_enter = monitor.update([_person(7, 150, 150)], timestamp=1.0)
    assert len(ev_enter) == 1
    assert ev_enter[0].zone_id == "active_zone"

    # Brief 0.5s detection dropout (< 1.0s grace) -> no premature exit
    assert monitor.update([], timestamp=1.5) == []
    assert monitor.active_track_ids("active_zone") == {7}

    # Prolonged disappearance (> 1.0s grace) -> exit emitted
    ev_exit = monitor.update([], timestamp=2.5)
    assert len(ev_exit) == 1
    assert ev_exit[0].event_type == "exit"
    assert ev_exit[0].track_id == 7


class _FakeTrackBoxes:
    def __init__(
        self,
        boxes: list[list[float]],
        confs: list[float],
        clss: list[int],
        ids: list[int] | None,
    ) -> None:
        self.xyxy = np.array(boxes, dtype=np.float32)
        self.conf = np.array(confs, dtype=np.float32)
        self.cls = np.array(clss, dtype=np.float32)
        self.id = np.array(ids, dtype=np.float32) if ids is not None else None


class _FakeTrackYOLO:
    def __init__(self, source: str) -> None:
        self.source = source
        self.calls: list[dict[str, Any]] = []

    def export(self, **kwargs: Any) -> str:
        raise RuntimeError("Force PyTorch fallback")

    def track(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return [
            SimpleNamespace(
                boxes=_FakeTrackBoxes(
                    boxes=[[100.0, 50.0, 160.0, 190.0], [10.0, 10.0, 50.0, 50.0]],
                    confs=[0.88, 0.92],
                    clss=[0, 2],  # person and car
                    ids=[42, 99],
                )
            )
        ]


def test_person_tracker_bytetrack_and_drawing(tmp_path: Path) -> None:
    fake_model = _FakeTrackYOLO(str(tmp_path / "models" / "yolo11n.pt"))
    tracker = PersonTracker(
        settings=Settings(log_dir=tmp_path / "logs"),
        model_path=tmp_path / "models" / "yolo11n.pt",
        use_openvino=True,
        model_loader=lambda src, task: fake_model,
    )
    assert tracker.backend == "pytorch"

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    tracks = tracker.update(frame)

    assert len(tracks) == 1
    assert tracks[0].track_id == 42
    assert tracks[0].bottom_center == (130, 190)
    assert fake_model.calls[0]["tracker"] == "bytetrack.yaml"
    assert fake_model.calls[0]["persist"] is True
    assert fake_model.calls[0]["classes"] == [0]

    zones = [_square_zone()]
    annotated = draw_zones(frame, zones, active_zone_ids={"restricted"})
    annotated = draw_tracks(annotated, tracks)
    assert annotated.shape == frame.shape
    assert np.any(annotated != 0)
    assert np.all(frame == 0)
