"""Tests for PersonDetector, OpenVINO fallback, motion gate, drawing, and benchmark."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np

from config.settings import Settings
from detection.person_detector import (
    Detection,
    PersonDetector,
    draw_detections,
)
from tools.benchmark import benchmark_video_file


class _FakeBoxes:
    def __init__(self, boxes: list[list[float]], confs: list[float], clss: list[int]) -> None:
        self.xyxy = np.array(boxes, dtype=np.float32)
        self.conf = np.array(confs, dtype=np.float32)
        self.cls = np.array(clss, dtype=np.float32)


class _FakeYOLO:
    def __init__(
        self,
        source: str,
        *,
        fail_export: bool = False,
        predict_items: list[tuple[list[float], float, int]] | None = None,
    ) -> None:
        self.source = source
        self.fail_export = fail_export
        self.predict_items = (
            predict_items
            if predict_items is not None
            else [
                ([10.0, 20.0, 110.0, 220.0], 0.91, 0),  # person
                ([120.0, 30.0, 180.0, 90.0], 0.95, 2),  # car (must be ignored)
                ([15.0, 25.0, 55.0, 95.0], 0.20, 0),    # low-confidence person
            ]
        )
        self.export_calls: list[dict[str, Any]] = []
        self.predict_calls: list[dict[str, Any]] = []

    def export(self, **kwargs: Any) -> str:
        self.export_calls.append(kwargs)
        if self.fail_export:
            raise RuntimeError("Simulated OpenVINO export error")
        out_dir = Path(self.source).parent / f"{Path(self.source).stem}_openvino_model"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "openvino_model.xml").write_text("<net/>", encoding="utf-8")
        return str(out_dir)

    def predict(self, **kwargs: Any) -> list[Any]:
        self.predict_calls.append(kwargs)
        boxes = [item[0] for item in self.predict_items]
        confs = [item[1] for item in self.predict_items]
        clss = [item[2] for item in self.predict_items]
        return [SimpleNamespace(boxes=_FakeBoxes(boxes, confs, clss))]


def test_openvino_export_and_cached_load(tmp_path: Path) -> None:
    pt_path = tmp_path / "models" / "yolo11n.pt"
    loaded_sources: list[str] = []
    fake_ov = _FakeYOLO(str(pt_path))

    def loader(src: str, task: str) -> _FakeYOLO:
        loaded_sources.append(src)
        return fake_ov if src.endswith(".pt") else _FakeYOLO(src)

    settings = Settings(log_dir=tmp_path / "logs")
    detector = PersonDetector(
        settings=settings,
        model_path=pt_path,
        imgsz=416,
        use_openvino=True,
        model_loader=loader,
    )

    assert detector.backend == "openvino"
    assert len(fake_ov.export_calls) == 1
    assert fake_ov.export_calls[0]["format"] == "openvino"
    assert fake_ov.export_calls[0]["imgsz"] == 416
    assert loaded_sources[-1].endswith("yolo11n_openvino_model")

    # Second initialization should load cached OpenVINO directory directly.
    loaded_sources.clear()
    detector2 = PersonDetector(
        settings=settings,
        model_path=pt_path,
        imgsz=416,
        use_openvino=True,
        model_loader=loader,
    )
    assert detector2.backend == "openvino"
    assert loaded_sources == [str(pt_path.parent / "yolo11n_openvino_model")]


def test_openvino_export_failure_falls_back_to_pytorch(tmp_path: Path) -> None:
    pt_path = tmp_path / "models" / "yolo11n.pt"
    fake_pt = _FakeYOLO(str(pt_path), fail_export=True)

    detector = PersonDetector(
        settings=Settings(log_dir=tmp_path / "logs"),
        model_path=pt_path,
        confidence=0.5,
        imgsz=320,
        use_openvino=True,
        motion_gate_enabled=False,
        model_loader=lambda src, task: fake_pt,
    )

    assert detector.backend == "pytorch"

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    result = detector.detect(frame)

    assert result.ran_inference is True
    assert len(result.detections) == 1
    det = result.detections[0]
    assert det.class_id == 0
    assert det.class_name == "person"
    assert det.bbox == (10, 20, 110, 220)
    assert fake_pt.predict_calls[0]["classes"] == [0]
    assert fake_pt.predict_calls[0]["conf"] == 0.5
    assert fake_pt.predict_calls[0]["imgsz"] == 320
    assert fake_pt.predict_calls[0]["device"] == "cpu"


def test_motion_gate_skips_idle_frames_and_triggers_on_movement(tmp_path: Path) -> None:
    clock = [100.0]

    def monotonic() -> float:
        return clock[0]

    fake_model = _FakeYOLO(str(tmp_path / "yolo11n.pt"))
    detector = PersonDetector(
        settings=Settings(
            log_dir=tmp_path / "logs",
            motion_warmup_frames=1,
            motion_hold_seconds=0.0,
        ),
        model_path=tmp_path / "yolo11n.pt",
        use_openvino=False,
        target_fps=4.0,
        motion_gate_enabled=True,
        motion_threshold=0.01,
        motion_hold_seconds=0.0,
        model_loader=lambda src, task: fake_model,
        monotonic_fn=monotonic,
    )

    static_frame = np.full((240, 320, 3), 80, dtype=np.uint8)

    # Warmup frame 1 runs YOLO
    res1 = detector.detect(static_frame)
    assert res1.ran_inference is True
    assert len(fake_model.predict_calls) == 1

    # Idle frames after warmup should NOT run YOLO
    for _ in range(5):
        clock[0] += 0.3
        idle_res = detector.detect(static_frame)
        assert idle_res.skipped is False
        assert idle_res.motion_detected is False
        assert idle_res.ran_inference is False
        assert idle_res.detections == []

    assert len(fake_model.predict_calls) == 1

    # Introduce clear motion -> YOLO must run again
    moving_frame = static_frame.copy()
    cv2.rectangle(moving_frame, (40, 40), (200, 200), (255, 255, 255), thickness=-1)
    clock[0] += 0.3
    move_res = detector.detect(moving_frame)
    assert move_res.motion_detected is True
    assert move_res.ran_inference is True
    assert len(move_res.detections) == 1
    assert len(fake_model.predict_calls) == 2


def test_frame_skipping_paces_target_fps(tmp_path: Path) -> None:
    clock = [10.0]
    fake_model = _FakeYOLO(str(tmp_path / "yolo11n.pt"))

    detector = PersonDetector(
        settings=Settings(log_dir=tmp_path / "logs"),
        model_path=tmp_path / "yolo11n.pt",
        use_openvino=False,
        target_fps=4.0,  # 1 check every 0.25s
        motion_gate_enabled=False,
        model_loader=lambda src, task: fake_model,
        monotonic_fn=lambda: clock[0],
    )

    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    # Simulate 30 FPS input for 0.50s (15 frames at 33ms spacing): expect ~2-3 inferences.
    for _ in range(15):
        res = detector.detect(frame)
        assert len(res.detections) == 1  # cached detections kept between skipped frames
        clock[0] += 1.0 / 30.0

    assert len(fake_model.predict_calls) in (2, 3)


def test_draw_detections_and_video_benchmark(tmp_path: Path) -> None:
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    det = Detection(bbox=(10, 15, 60, 100), confidence=0.88)
    drawn = draw_detections(frame, [det])

    assert drawn.shape == frame.shape
    assert np.any(drawn != 0)
    assert np.all(frame == 0)  # original frame is untouched

    video_path = tmp_path / "bench.avi"
    writer = cv2.VideoWriter(
        str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (160, 120)
    )
    assert writer.isOpened()
    for i in range(6):
        img = np.full((120, 160, 3), (i * 30) % 255, dtype=np.uint8)
        writer.write(img)
    writer.release()

    fake_model = _FakeYOLO(str(tmp_path / "yolo11n.pt"))
    detector = PersonDetector(
        settings=Settings(log_dir=tmp_path / "logs"),
        model_path=tmp_path / "yolo11n.pt",
        use_openvino=False,
        target_fps=5.0,
        motion_gate_enabled=False,
        model_loader=lambda src, task: fake_model,
    )

    summary = benchmark_video_file(
        video_path,
        detector,
        max_frames=6,
        preview=False,
    )
    assert summary.frames_read == 6
    assert summary.inferences_run >= 1
    assert summary.effective_fps > 0.0
