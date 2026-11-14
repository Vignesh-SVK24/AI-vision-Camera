"""Local face matcher (cosine similarity) and per-track identity voting/cache."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

import numpy as np

from config.settings import Settings, get_settings
from faces.face_engine import DetectedFace, FaceEngine, l2_normalize
from storage.db import Database
from tracking.tracker import TrackedPerson

logger = logging.getLogger(__name__)

UNKNOWN_NAME = "Unknown"


@dataclass(frozen=True)
class FaceMatch:
    """Result of matching a face embedding against enrolled people."""

    name: str
    score: float
    person_id: Optional[int]
    matched: bool


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two 1-D vectors."""
    va = l2_normalize(a)
    vb = l2_normalize(b)
    if va.size == 0 or vb.size == 0 or va.shape != vb.shape:
        return 0.0
    return float(np.clip(np.dot(va, vb), -1.0, 1.0))


@dataclass
class _EnrolledEmbedding:
    person_id: int
    name: str
    vector: np.ndarray


class FaceMatcher:
    """Matches face embeddings against locally enrolled people in SQLite."""

    def __init__(
        self,
        db: Database,
        settings: Optional[Settings] = None,
        *,
        face_engine: Optional[FaceEngine] = None,
        threshold: Optional[float] = None,
    ) -> None:
        cfg = settings or get_settings()
        self._db = db
        self._engine = face_engine or FaceEngine(settings=cfg)
        self._threshold = (
            cfg.face_match_threshold if threshold is None else float(threshold)
        )
        self._enrolled: list[_EnrolledEmbedding] = []
        self.reload()

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = float(value)

    @property
    def enrolled_count(self) -> int:
        return len(self._enrolled)

    def reload(self) -> int:
        """Reload enrolled embeddings from SQLite."""
        rows = self._db.list_face_embeddings()
        loaded: list[_EnrolledEmbedding] = []
        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if vec.size == 0:
                continue
            loaded.append(
                _EnrolledEmbedding(
                    person_id=int(row["person_id"]),
                    name=str(row["name"]),
                    vector=l2_normalize(vec),
                )
            )
        self._enrolled = loaded
        return len(self._enrolled)

    def match_embedding(self, embedding: np.ndarray) -> FaceMatch:
        """Match an embedding vector against enrolled embeddings."""
        if not self._enrolled:
            return FaceMatch(name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False)

        query = l2_normalize(embedding)
        best_score = -1.0
        best_entry: Optional[_EnrolledEmbedding] = None

        for entry in self._enrolled:
            if entry.vector.shape != query.shape:
                continue
            score = float(np.clip(np.dot(query, entry.vector), -1.0, 1.0))
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is None:
            return FaceMatch(name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False)

        if best_score >= self._threshold:
            return FaceMatch(
                name=best_entry.name,
                score=best_score,
                person_id=best_entry.person_id,
                matched=True,
            )

        return FaceMatch(
            name=UNKNOWN_NAME,
            score=best_score,
            person_id=None,
            matched=False,
        )

    def match_image(self, image: np.ndarray) -> Optional[FaceMatch]:
        """Detect the primary face in an image/crop and match it."""
        if image is None or image.size == 0:
            return None

        faces = self._engine.detect_faces(image)
        if not faces:
            return None

        largest_face = max(faces, key=lambda f: f.bbox[2] * f.bbox[3])
        embedding = self._engine.extract_embedding(image, largest_face)
        return self.match_embedding(embedding)


@dataclass
class _TrackVoteState:
    attempts: int = 0
    samples: list[FaceMatch] = field(default_factory=list)
    cached: Optional[FaceMatch] = None


class TrackIdentityManager:
    """Runs face matching on large-enough person crops for a few frames, votes, and caches."""

    def __init__(
        self,
        matcher: FaceMatcher,
        settings: Optional[Settings] = None,
        *,
        min_crop_width: Optional[int] = None,
        min_crop_height: Optional[int] = None,
        max_frames_per_track: Optional[int] = None,
        min_votes: Optional[int] = None,
    ) -> None:
        cfg = settings or get_settings()
        self._matcher = matcher
        self._min_crop_width = (
            cfg.face_min_person_crop_width
            if min_crop_width is None
            else int(min_crop_width)
        )
        self._min_crop_height = (
            cfg.face_min_person_crop_height
            if min_crop_height is None
            else int(min_crop_height)
        )
        self._max_frames = max(
            1,
            cfg.face_max_frames_per_track
            if max_frames_per_track is None
            else int(max_frames_per_track),
        )
        self._min_votes = max(
            1,
            cfg.face_vote_min_samples if min_votes is None else int(min_votes),
        )
        self._tracks: dict[int, _TrackVoteState] = {}

    def get_cached_identity(self, track_id: int) -> Optional[FaceMatch]:
        state = self._tracks.get(track_id)
        return state.cached if state is not None else None

    def _finalize_vote(self, state: _TrackVoteState) -> FaceMatch:
        if not state.samples:
            return FaceMatch(name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False)

        known_votes = Counter(
            (m.person_id, m.name) for m in state.samples if m.matched and m.person_id is not None
        )
        if known_votes:
            (best_pid, best_name), vote_count = known_votes.most_common(1)[0]
            if vote_count >= self._min_votes or (
                state.attempts >= self._max_frames
                and vote_count > (len(state.samples) / 2.0)
            ):
                avg_score = float(
                    np.mean(
                        [m.score for m in state.samples if m.person_id == best_pid]
                    )
                )
                return FaceMatch(
                    name=best_name,
                    score=avg_score,
                    person_id=best_pid,
                    matched=True,
                )

        best_unknown_score = max(m.score for m in state.samples)
        return FaceMatch(
            name=UNKNOWN_NAME,
            score=best_unknown_score,
            person_id=None,
            matched=False,
        )

    def identify_track(self, frame: np.ndarray, track: TrackedPerson) -> FaceMatch:
        """Identify a tracked person, returning cached vote once finalized."""
        state = self._tracks.setdefault(track.track_id, _TrackVoteState())
        if state.cached is not None:
            return state.cached

        x1, y1, x2, y2 = track.bbox
        crop_w = x2 - x1
        crop_h = y2 - y1
        if crop_w < self._min_crop_width or crop_h < self._min_crop_height:
            return FaceMatch(name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False)

        if state.attempts >= self._max_frames:
            state.cached = self._finalize_vote(state)
            return state.cached

        h_img, w_img = frame.shape[:2]
        cx1 = max(0, x1)
        cy1 = max(0, y1)
        cx2 = min(w_img, x2)
        cy2 = min(h_img, y2)
        if cx2 <= cx1 or cy2 <= cy1:
            return FaceMatch(name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False)

        person_crop = frame[cy1:cy2, cx1:cx2]
        state.attempts += 1

        match = self._matcher.match_image(person_crop)
        if match is not None:
            state.samples.append(match)

        # Lock cache early if known identity reaches min_votes, or when budget ends.
        known_counts = Counter(
            (m.person_id, m.name) for m in state.samples if m.matched and m.person_id is not None
        )
        if known_counts:
            (_, _), top_votes = known_counts.most_common(1)[0]
            if top_votes >= self._min_votes:
                state.cached = self._finalize_vote(state)
                return state.cached

        if state.attempts >= self._max_frames:
            state.cached = self._finalize_vote(state)
            return state.cached

        return match if match is not None else FaceMatch(
            name=UNKNOWN_NAME, score=0.0, person_id=None, matched=False
        )

    def clear_track(self, track_id: int) -> None:
        self._tracks.pop(track_id, None)

    def prune_tracks(self, active_track_ids: Iterable[int]) -> None:
        active = set(active_track_ids)
        for tid in list(self._tracks.keys()):
            if tid not in active:
                self._tracks.pop(tid, None)
