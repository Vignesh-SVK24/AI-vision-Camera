"""Privacy-first local face enrollment and deletion with consent enforcement."""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from config.settings import BASE_DIR, Settings, get_settings
from faces.face_engine import (
    MODEL_NAME_SFACE,
    DetectedFace,
    FaceEngine,
    compute_blur_score,
    l2_normalize,
)
from storage.db import Database

logger = logging.getLogger(__name__)


class EnrollmentError(ValueError):
    """Raised when consent or photo quality requirements are not met."""


@dataclass(frozen=True)
class ConsentRecord:
    """Explicit consent record required before storing a person's face data."""

    date: str
    note: str

    @classmethod
    def coerce(cls, value: Any) -> "ConsentRecord":
        if value is None:
            raise EnrollmentError(
                "Enrollment refused: a consent record with date and note is required."
            )
        if isinstance(value, cls):
            raw_date, raw_note = value.date, value.note
        elif isinstance(value, dict):
            raw_date = str(value.get("date", ""))
            raw_note = str(value.get("note", ""))
        else:
            raise EnrollmentError(
                "Enrollment refused: consent must be a ConsentRecord or {'date', 'note'} dict."
            )

        clean_date = raw_date.strip()
        clean_note = raw_note.strip()
        if not clean_date or not clean_note:
            raise EnrollmentError(
                "Enrollment refused: both consent date and consent note must be non-empty."
            )
        try:
            date.fromisoformat(clean_date[:10])
        except ValueError as exc:
            raise EnrollmentError(
                f"Enrollment refused: invalid consent date '{clean_date}' (expected YYYY-MM-DD)."
            ) from exc

        return cls(date=clean_date, note=clean_note)


@dataclass(frozen=True)
class EnrollmentResult:
    """Outcome of enrolling a person into local SQLite storage."""

    person_id: int
    name: str
    enrolled_embeddings: int
    saved_photos: list[str]
    rejected_reasons: dict[str, str]


def _load_bgr_image(item: str | Path | np.ndarray) -> tuple[str, np.ndarray]:
    if isinstance(item, np.ndarray):
        return "in_memory_image", item
    path = Path(item)
    if not path.exists():
        raise EnrollmentError(f"Image file not found: {path}")
    img = cv2.imread(str(path))
    if img is None:
        raise EnrollmentError(f"Failed to decode image file: {path}")
    return str(path), img


def validate_enrollment_image(
    image: np.ndarray,
    face_engine: FaceEngine,
    *,
    min_face_size: int = 80,
    min_blur_score: float = 60.0,
) -> tuple[DetectedFace, float, np.ndarray]:
    """Validate single-face, minimum size (>=80px), sharpness, and return embedding."""
    if image is None or image.size == 0:
        raise EnrollmentError("Empty image provided.")

    faces = face_engine.detect_faces(image)
    if len(faces) == 0:
        raise EnrollmentError("No face detected in photo.")
    if len(faces) > 1:
        raise EnrollmentError(f"Multiple faces detected ({len(faces)}); expected exactly 1.")

    face = faces[0]
    _, _, w, h = face.bbox
    if w < min_face_size or h < min_face_size:
        raise EnrollmentError(
            f"Face too small ({w}x{h}px; minimum required is {min_face_size}x{min_face_size}px)."
        )

    blur_score = compute_blur_score(image, face.bbox)
    if blur_score < min_blur_score:
        raise EnrollmentError(
            f"Photo too blurry (sharpness={blur_score:.1f} < minimum {min_blur_score:.1f})."
        )

    embedding = l2_normalize(face_engine.extract_embedding(image, face))
    return face, blur_score, embedding


def add_person(
    db: Database,
    name: str,
    images: Sequence[str | Path | np.ndarray],
    consent: ConsentRecord | dict[str, Any] | None,
    *,
    face_engine: Optional[FaceEngine] = None,
    settings: Optional[Settings] = None,
    faces_data_dir: Optional[Path | str] = None,
    min_face_size: Optional[int] = None,
    min_blur_score: Optional[float] = None,
    is_trusted: bool = True,
    notes: Optional[str] = None,
    save_photos: bool = True,
) -> EnrollmentResult:
    """Enroll a person locally with validated face embeddings and explicit consent."""
    clean_name = name.strip()
    if not clean_name:
        raise EnrollmentError("Person name must not be empty.")

    consent_rec = ConsentRecord.coerce(consent)
    if not images:
        raise EnrollmentError("At least one enrollment image is required.")

    cfg = settings or get_settings()
    engine = face_engine or FaceEngine(settings=cfg)
    min_px = cfg.face_min_size_px if min_face_size is None else int(min_face_size)
    min_blur = (
        cfg.face_blur_min_laplacian
        if min_blur_score is None
        else float(min_blur_score)
    )
    data_dir = (
        Path(faces_data_dir) if faces_data_dir is not None else cfg.faces_data_dir
    )

    accepted: list[tuple[str, np.ndarray, float, np.ndarray]] = []
    rejected: dict[str, str] = {}

    for idx, item in enumerate(images):
        label = str(item) if not isinstance(item, np.ndarray) else f"image_{idx}"
        try:
            label, bgr = _load_bgr_image(item)
            _, blur_score, emb = validate_enrollment_image(
                bgr,
                engine,
                min_face_size=min_px,
                min_blur_score=min_blur,
            )
            accepted.append((label, bgr, blur_score, emb))
        except EnrollmentError as exc:
            rejected[label] = str(exc)

    if not accepted:
        reasons = "; ".join(f"{k}: {v}" for k, v in rejected.items())
        raise EnrollmentError(f"All enrollment images were rejected ({reasons}).")

    person_id = db.create_person(
        name=clean_name,
        consent_date=consent_rec.date,
        consent_note=consent_rec.note,
        is_trusted=is_trusted,
        notes=notes,
    )

    saved_photos: list[str] = []
    person_dir = data_dir / f"person_{person_id}"
    if save_photos:
        person_dir.mkdir(parents=True, exist_ok=True)

    for idx, (_, bgr, blur_score, emb) in enumerate(accepted):
        db.add_face_embedding(
            person_id=person_id,
            embedding_bytes=emb.astype(np.float32).tobytes(),
            model_name=MODEL_NAME_SFACE,
            quality_score=blur_score,
        )
        if save_photos:
            photo_path = person_dir / f"enroll_{idx}_{uuid.uuid4().hex[:8]}.jpg"
            cv2.imwrite(str(photo_path), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            stored = _format_local_path(photo_path)
            db.add_person_photo(person_id=person_id, file_path=stored)
            saved_photos.append(stored)

    db.record_audit(
        actor="enrollment",
        action="enroll_person",
        target_type="person",
        target_id=str(person_id),
        details={
            "name": clean_name,
            "consent_date": consent_rec.date,
            "embeddings_added": len(accepted),
            "rejected_count": len(rejected),
        },
    )
    logger.info(
        "Enrolled person '%s' (id=%d) with %d embedding(s), %d rejected.",
        clean_name,
        person_id,
        len(accepted),
        len(rejected),
    )

    return EnrollmentResult(
        person_id=person_id,
        name=clean_name,
        enrolled_embeddings=len(accepted),
        saved_photos=saved_photos,
        rejected_reasons=rejected,
    )


def delete_person(
    db: Database,
    identifier: int | str,
    *,
    settings: Optional[Settings] = None,
    faces_data_dir: Optional[Path | str] = None,
) -> int:
    """Delete a person by ID or name, removing embeddings and stored local photos."""
    cfg = settings or get_settings()
    data_dir = (
        Path(faces_data_dir) if faces_data_dir is not None else cfg.faces_data_dir
    )

    if isinstance(identifier, int):
        target_ids = [identifier]
    else:
        matches = db.find_people_by_name(identifier)
        target_ids = [int(m["id"]) for m in matches]

    deleted_count = 0
    for pid in target_ids:
        existed, photo_paths = db.delete_person_records(pid)
        if not existed:
            continue
        deleted_count += 1

        for raw_path in photo_paths:
            resolved = _resolve_local_path(raw_path)
            if resolved.exists() and resolved.is_file():
                try:
                    resolved.unlink()
                except OSError as exc:
                    logger.warning("Failed to remove photo '%s': %s", resolved, exc)

        person_dir = data_dir / f"person_{pid}"
        if person_dir.exists() and person_dir.is_dir():
            shutil.rmtree(person_dir, ignore_errors=True)

        db.record_audit(
            actor="enrollment",
            action="delete_person",
            target_type="person",
            target_id=str(pid),
            details={"photos_removed": len(photo_paths)},
        )
        logger.info(
            "Deleted person id=%d, embeddings, and %d stored photo(s).",
            pid,
            len(photo_paths),
        )

    return deleted_count


def _format_local_path(path: Path, base_dir: Path = BASE_DIR) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_local_path(stored_path: str, base_dir: Path = BASE_DIR) -> Path:
    path = Path(stored_path)
    if path.is_absolute():
        return path
    return base_dir / path
