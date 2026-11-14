"""Pydantic v2 request and response schemas for the local-first backend API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool


class HealthResponse(BaseModel):
    status: str
    db: str
    schema_version: int


class CameraStatusResponse(BaseModel):
    camera_name: str
    status: str
    last_status_change: Optional[str] = None
    last_event_at: Optional[str] = None
    last_event_type: Optional[str] = None
    total_events: int


class EventItem(BaseModel):
    id: int
    event_type: str
    created_at: str
    track_id: Optional[int] = None
    zone_id: Optional[str] = None
    zone_name: Optional[str] = None
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    confidence: Optional[float] = None
    snapshot_url: Optional[str] = None
    message: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class EventListResponse(BaseModel):
    events: list[EventItem]
    count: int


class PersonItem(BaseModel):
    id: int
    name: str
    is_trusted: bool
    consent_date: Optional[str] = None
    consent_note: Optional[str] = None
    embedding_count: int = 0
    photo_count: int = 0
    created_at: str


class PersonEnrollResponse(BaseModel):
    person_id: int
    name: str
    enrolled_embeddings: int
    rejected_reasons: dict[str, str]


class DeletePersonResponse(BaseModel):
    deleted: bool
    person_id: int


class ZonePayload(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    dwell_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)
    points: list[list[int]] = Field(min_length=3)
    color: list[int] = Field(default_factory=lambda: [0, 165, 255], min_length=3, max_length=3)

    @field_validator("points")
    @classmethod
    def validate_points(cls, pts: list[list[int]]) -> list[list[int]]:
        for pt in pts:
            if len(pt) != 2:
                raise ValueError("Each polygon point must be [x, y].")
        return pts

    @field_validator("color")
    @classmethod
    def validate_color(cls, col: list[int]) -> list[int]:
        for ch in col:
            if ch < 0 or ch > 255:
                raise ValueError("Color channels must be between 0 and 255.")
        return col


class ZonesUpdateRequest(StrictModel):
    zones: list[ZonePayload]


class ZonesResponse(BaseModel):
    zones: list[ZonePayload]


class SettingsResponse(BaseModel):
    app_name: str
    camera_name: str
    detection_confidence: float
    detection_imgsz: int
    detection_target_fps: float
    motion_gate_enabled: bool
    face_match_threshold: float
    event_track_cooldown_seconds: float
    event_zone_cooldown_seconds: float
    retention_days: int
    telegram_enabled: bool
    telegram_configured: bool
    alert_on_unknown_person: bool
    alert_on_known_person: bool
    alert_on_zone_intrusion: bool
    overrides: dict[str, str]


class SettingsUpdateRequest(StrictModel):
    overrides: dict[str, str] = Field(min_length=1)


class NotificationItem(BaseModel):
    id: int
    event_id: int
    event_type: str
    event_created_at: str
    notified: bool
    synced: bool
    notify_attempts: int
    last_error: Optional[str] = None
    zone_name: Optional[str] = None
    message: Optional[str] = None


class AuditLogItem(BaseModel):
    id: int
    actor: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details_json: Optional[str] = None
    created_at: str
