"""Tests for privacy-first face enrollment, quality checks, matching, voting, and deletion."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from config.settings import Settings
from faces.enrollment import ConsentRecord, EnrollmentError, add_person, delete_person
from faces.face_engine import DetectedFace, FaceEngine, l2_normalize
from faces.matcher import UNKNOWN_NAME, FaceMatcher, TrackIdentityManager
from storage.db import Database
from tracking.tracker import TrackedPerson


class _FakeFaceEngine(FaceEngine):
    """Deterministic in-memory FaceEngine avoiding external ONNX downloads in tests."""

    def __init__(self, *, mode: str = "single", embedding: np.ndarray | None = None) -> None:
        super().__init__(
            yunet_model_path="unused.onnx",
            sface_model_path="unused.onnx",
        )
        self.mode = mode
        self.embedding = (
            l2_normalize(embedding)
            if embedding is not None
            else l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32))
        )
        self.detect_calls = 0

    def detect_faces(self, image: np.ndarray) -> list[DetectedFace]:
        self.detect_calls += 1
        if self.mode == "none":
            return []
        if self.mode == "multiple":
            return [
                DetectedFace((10, 10, 100, 100), 0.95, np.zeros(15, dtype=np.float32)),
                DetectedFace((120, 10, 100, 100), 0.92, np.zeros(15, dtype=np.float32)),
            ]
        if self.mode == "small":
            return [DetectedFace((20, 20, 60, 60), 0.95, np.zeros(15, dtype=np.float32))]
        return [DetectedFace((20, 20, 100, 100), 0.98, np.zeros(15, dtype=np.float32))]

    def extract_embedding(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        return self.embedding.copy()


def _sharp_image(width: int = 240, height: int = 240) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # High-contrast checkerboard pattern produces high Laplacian variance.
    for y in range(0, height, 8):
        for x in range(0, width, 8):
            if ((x // 8) + (y // 8)) % 2 == 0:
                img[y : y + 8, x : x + 8] = (255, 255, 255)
    return img


def _blurry_image(width: int = 240, height: int = 240) -> np.ndarray:
    return np.full((height, width, 3), 128, dtype=np.uint8)


def test_enrollment_requires_valid_consent(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "faces.db",
        faces_data_dir=tmp_path / "faces_data",
    )
    img = _sharp_image()
    engine = _FakeFaceEngine()

    with Database(db_path=settings.db_path, settings=settings) as db:
        with pytest.raises(EnrollmentError):
            add_person(db, "Alice", [img], None, face_engine=engine, settings=settings)
        with pytest.raises(EnrollmentError):
            add_person(
                db,
                "Alice",
                [img],
                {"date": "2026-11-12", "note": "   "},
                face_engine=engine,
                settings=settings,
            )
        with pytest.raises(EnrollmentError):
            add_person(
                db,
                "Alice",
                [img],
                {"date": "not-a-date", "note": "Signed"},
                face_engine=engine,
                settings=settings,
            )
        assert db.list_people() == []


def test_enrollment_rejects_bad_photos(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "faces.db",
        faces_data_dir=tmp_path / "faces_data",
        face_min_size_px=80,
        face_blur_min_laplacian=60.0,
    )
    consent = ConsentRecord(date="2026-11-12", note="Family consent")
    sharp = _sharp_image()
    blurry = _blurry_image()

    with Database(db_path=settings.db_path, settings=settings) as db:
        with pytest.raises(EnrollmentError, match="No face"):
            add_person(db, "Alice", [sharp], consent, face_engine=_FakeFaceEngine(mode="none"), settings=settings)
        with pytest.raises(EnrollmentError, match="Multiple faces"):
            add_person(db, "Alice", [sharp], consent, face_engine=_FakeFaceEngine(mode="multiple"), settings=settings)
        with pytest.raises(EnrollmentError, match="too small"):
            add_person(db, "Alice", [sharp], consent, face_engine=_FakeFaceEngine(mode="small"), settings=settings)
        with pytest.raises(EnrollmentError, match="blurry"):
            add_person(db, "Alice", [blurry], consent, face_engine=_FakeFaceEngine(mode="single"), settings=settings)
        assert db.list_people() == []


def test_enroll_match_unknown_and_delete_person(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "faces.db",
        faces_data_dir=tmp_path / "faces_data",
        face_match_threshold=0.50,
    )
    consent = ConsentRecord(date="2026-11-12", note="Homeowner consent")
    alice_vec = np.array([3.0, 4.0, 0.0, 0.0], dtype=np.float32)
    stranger_vec = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)

    alice_engine = _FakeFaceEngine(mode="single", embedding=alice_vec)
    stranger_engine = _FakeFaceEngine(mode="single", embedding=stranger_vec)
    sharp = _sharp_image()

    with Database(db_path=settings.db_path, settings=settings) as db:
        result = add_person(
            db,
            name="Alice",
            images=[sharp],
            consent=consent,
            face_engine=alice_engine,
            settings=settings,
        )
        assert result.enrolled_embeddings == 1
        assert len(result.saved_photos) == 1
        saved_photo_path = Path(result.saved_photos[0])
        assert saved_photo_path.exists()

        # Verify stored embedding is L2-normalized
        rows = db.list_face_embeddings()
        assert len(rows) == 1
        stored_vec = np.frombuffer(rows[0]["embedding"], dtype=np.float32)
        assert np.isclose(np.linalg.norm(stored_vec), 1.0, atol=1e-5)

        # Enrolled person matches by name
        matcher_alice = FaceMatcher(db, settings=settings, face_engine=alice_engine)
        match = matcher_alice.match_image(sharp)
        assert match is not None
        assert match.matched is True
        assert match.name == "Alice"
        assert match.score > 0.99

        # Stranger returns Unknown plus cosine score
        matcher_stranger = FaceMatcher(db, settings=settings, face_engine=stranger_engine)
        stranger_match = matcher_stranger.match_image(sharp)
        assert stranger_match is not None
        assert stranger_match.matched is False
        assert stranger_match.name == UNKNOWN_NAME
        assert stranger_match.score < 0.50

        # Deleting person removes DB rows, embeddings, and stored photos
        deleted = delete_person(db, result.person_id, settings=settings)
        assert deleted == 1
        assert db.list_people() == []
        assert db.list_face_embeddings() == []
        assert not saved_photo_path.exists()

        matcher_alice.reload()
        after_delete = matcher_alice.match_image(sharp)
        assert after_delete is not None
        assert after_delete.name == UNKNOWN_NAME


def test_track_identity_manager_skips_small_crops_votes_and_caches(tmp_path: Path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        db_path=tmp_path / "faces.db",
        faces_data_dir=tmp_path / "faces_data",
        face_match_threshold=0.50,
        face_min_person_crop_width=80,
        face_min_person_crop_height=120,
        face_max_frames_per_track=4,
        face_vote_min_samples=2,
    )
    alice_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    engine = _FakeFaceEngine(mode="single", embedding=alice_vec)
    sharp = _sharp_image(400, 400)

    with Database(db_path=settings.db_path, settings=settings) as db:
        add_person(
            db,
            name="Alice",
            images=[sharp],
            consent=ConsentRecord(date="2026-11-12", note="Consent"),
            face_engine=engine,
            settings=settings,
        )
        matcher = FaceMatcher(db, settings=settings, face_engine=engine)
        id_mgr = TrackIdentityManager(matcher, settings=settings)
        engine.detect_calls = 0

        # Small crop is skipped without calling FaceEngine
        small_track = TrackedPerson(track_id=1, bbox=(10, 10, 50, 90), confidence=0.9)
        res_small = id_mgr.identify_track(sharp, small_track)
        assert res_small.name == UNKNOWN_NAME
        assert engine.detect_calls == 0

        # Large crop runs for min_votes=2 frames, caches identity, then stops calling FaceEngine
        large_track = TrackedPerson(track_id=2, bbox=(20, 20, 220, 320), confidence=0.95)
        for _ in range(10):
            res = id_mgr.identify_track(sharp, large_track)
            assert res.name == "Alice"

        assert engine.detect_calls == 2
        cached = id_mgr.get_cached_identity(2)
        assert cached is not None and cached.name == "Alice"
