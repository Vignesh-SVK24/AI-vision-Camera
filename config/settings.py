"""Application configuration loaded from environment variables and .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: Optional[str], default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return default


@dataclass(frozen=True)
class Settings:
    """Typed runtime settings with safe defaults and masked secret display."""

    app_name: str = "Vision Surveillance"
    app_env: str = "development"

    # Logging
    log_level: str = "INFO"
    log_dir: Path = field(default_factory=lambda: BASE_DIR / "logs")
    log_file: str = "app.log"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5

    # Camera / RTSP
    camera_ip: str = ""
    camera_port: int = 554
    camera_user: str = ""
    camera_pass: str = field(default="", repr=False)
    camera_main_stream: str = "stream1"
    camera_sub_stream: str = "stream2"
    camera_source: str = ""

    # RTSP recovery & watchdog
    rtsp_reconnect_initial_delay: float = 1.0
    rtsp_reconnect_max_delay: float = 30.0
    rtsp_stale_timeout: float = 5.0

    # Person detection (CPU / OpenVINO)
    detection_model: str = "models/yolo11n.pt"
    detection_confidence: float = 0.45
    detection_imgsz: int = 640
    detection_target_fps: float = 4.0
    detection_frame_skip: int = 0
    detection_use_openvino: bool = True

    # Motion gate (MOG2 background subtraction)
    motion_gate_enabled: bool = True
    motion_threshold: float = 0.005
    motion_var_threshold: float = 25.0
    motion_history: int = 300
    motion_downscale_width: int = 320
    motion_hold_seconds: float = 0.5
    motion_warmup_frames: int = 2

    # Storage, snapshots & retention
    db_path: Path = field(default_factory=lambda: BASE_DIR / "surveillance.db")
    snapshots_dir: Path = field(default_factory=lambda: BASE_DIR / "snapshots")
    snapshot_max_width: int = 480
    snapshot_quality: int = 80
    event_track_cooldown_seconds: float = 60.0
    event_zone_cooldown_seconds: float = 60.0
    event_absence_reset_seconds: float = 5.0
    retention_days: int = 30
    retention_run_hour: int = 2
    retention_run_minute: int = 0

    # Face detection & recognition (local YuNet + SFace)
    face_yunet_model: Path = field(
        default_factory=lambda: BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
    )
    face_sface_model: Path = field(
        default_factory=lambda: BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
    )
    faces_data_dir: Path = field(default_factory=lambda: BASE_DIR / "faces_data")
    face_match_threshold: float = 0.40
    face_min_size_px: int = 80
    face_blur_min_laplacian: float = 60.0
    face_min_person_crop_width: int = 80
    face_min_person_crop_height: int = 120
    face_max_frames_per_track: int = 5
    face_vote_min_samples: int = 2

    # Telegram notifications & outbox worker
    camera_name: str = "Front Camera"
    telegram_enabled: bool = True
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    alert_on_unknown_person: bool = True
    alert_on_known_person: bool = False
    alert_on_zone_intrusion: bool = True
    alert_on_camera_offline: bool = True
    alert_on_camera_online: bool = True
    alert_on_system_error: bool = True
    telegram_poll_interval: float = 2.0
    telegram_backoff_initial: float = 2.0
    telegram_backoff_max: float = 60.0
    telegram_rate_limit_per_min: int = 20
    telegram_min_interval_seconds: float = 0.5

    # Local-first Backend API & Auth
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    jwt_secret_key: str = field(default="", repr=False)
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_window_seconds: int = 60
    api_rate_limit_max_requests: int = 120
    api_write_rate_limit_max_requests: int = 30
    api_rate_limit_window_seconds: int = 60
    api_max_request_bytes: int = 1024 * 1024
    cors_allowed_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
    upload_max_bytes: int = 5 * 1024 * 1024
    cloud_sync: bool = False
    supabase_url: str = ""
    supabase_key: str = field(default="", repr=False)
    supabase_bucket: str = "event-thumbnails"
    supabase_table: str = "events"
    cloud_sync_retention_days: int = 30
    cloud_sync_poll_interval: float = 5.0
    cloud_sync_backoff_initial: float = 2.0
    cloud_sync_backoff_max: float = 60.0
    cloud_sync_cleanup_hour: int = 3
    cloud_sync_thumbnail_max_width: int = 320
    cloud_sync_thumbnail_quality: int = 70
    engine_frame_stall_seconds: float = 30.0
    disk_low_warn_mb: int = 2048
    disk_critical_stop_mb: int = 512
    disk_check_interval_seconds: float = 30.0
    backup_dir: Path = BASE_DIR / "backups"
    backup_keep_count: int = 8

    @property
    def log_path(self) -> Path:
        return self.log_dir / self.log_file

    def secrets(self) -> tuple[str, ...]:
        """Return non-empty secret strings that must never appear in logs."""
        values = [
            self.camera_pass,
            self.telegram_bot_token,
            self.jwt_secret_key,
            self.supabase_key,
        ]
        return tuple(v for v in values if v)


def load_settings(env_file: Optional[Path | str] = None, override: bool = False) -> Settings:
    """Load settings from `.env` (if present) and environment variables."""
    target_env = Path(env_file) if env_file is not None else BASE_DIR / ".env"
    if target_env.exists():
        load_dotenv(dotenv_path=target_env, override=override)

    raw_log_dir = os.getenv("LOG_DIR", "logs").strip() or "logs"
    log_dir_path = Path(raw_log_dir)
    if not log_dir_path.is_absolute():
        log_dir_path = BASE_DIR / log_dir_path

    raw_db_path = os.getenv("DB_PATH", "surveillance.db").strip() or "surveillance.db"
    db_path = Path(raw_db_path)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    raw_snapshots_dir = os.getenv("SNAPSHOTS_DIR", "snapshots").strip() or "snapshots"
    snapshots_dir = Path(raw_snapshots_dir)
    if not snapshots_dir.is_absolute():
        snapshots_dir = BASE_DIR / snapshots_dir

    def _resolve_rel(env_name: str, default_rel: str) -> Path:
        raw = os.getenv(env_name, default_rel).strip() or default_rel
        p = Path(raw)
        return p if p.is_absolute() else BASE_DIR / p

    face_yunet_model = _resolve_rel(
        "FACE_YUNET_MODEL", "models/face_detection_yunet_2023mar.onnx"
    )
    face_sface_model = _resolve_rel(
        "FACE_SFACE_MODEL", "models/face_recognition_sface_2021dec.onnx"
    )
    faces_data_dir = _resolve_rel("FACES_DATA_DIR", "faces_data")

    return Settings(
        app_name=os.getenv("APP_NAME", "Vision Surveillance").strip() or "Vision Surveillance",
        app_env=os.getenv("APP_ENV", "development").strip() or "development",
        log_level=(os.getenv("LOG_LEVEL", "INFO").strip() or "INFO").upper(),
        log_dir=log_dir_path,
        log_file=os.getenv("LOG_FILE", "app.log").strip() or "app.log",
        log_max_bytes=_parse_int(os.getenv("LOG_MAX_BYTES"), 5 * 1024 * 1024),
        log_backup_count=_parse_int(os.getenv("LOG_BACKUP_COUNT"), 5),
        camera_ip=os.getenv("CAMERA_IP", "").strip(),
        camera_port=_parse_int(os.getenv("CAMERA_PORT"), 554),
        camera_user=os.getenv("CAMERA_USER", "").strip(),
        camera_pass=os.getenv("CAMERA_PASS", ""),
        camera_main_stream=os.getenv("CAMERA_MAIN_STREAM", "stream1").strip() or "stream1",
        camera_sub_stream=os.getenv("CAMERA_SUB_STREAM", "stream2").strip() or "stream2",
        camera_source=os.getenv("CAMERA_SOURCE", "").strip(),
        rtsp_reconnect_initial_delay=max(
            0.05, _parse_float(os.getenv("RTSP_RECONNECT_INITIAL_DELAY"), 1.0)
        ),
        rtsp_reconnect_max_delay=max(
            0.1, _parse_float(os.getenv("RTSP_RECONNECT_MAX_DELAY"), 30.0)
        ),
        rtsp_stale_timeout=max(
            0.1, _parse_float(os.getenv("RTSP_STALE_TIMEOUT"), 5.0)
        ),
        detection_model=os.getenv("DETECTION_MODEL", "models/yolo11n.pt").strip()
        or "models/yolo11n.pt",
        detection_confidence=min(
            1.0, max(0.01, _parse_float(os.getenv("DETECTION_CONFIDENCE"), 0.45))
        ),
        detection_imgsz=max(64, _parse_int(os.getenv("DETECTION_IMGSZ"), 640)),
        detection_target_fps=max(
            0.1, _parse_float(os.getenv("DETECTION_TARGET_FPS"), 4.0)
        ),
        detection_frame_skip=max(
            0, _parse_int(os.getenv("DETECTION_FRAME_SKIP"), 0)
        ),
        detection_use_openvino=_parse_bool(
            os.getenv("DETECTION_USE_OPENVINO"), True
        ),
        motion_gate_enabled=_parse_bool(os.getenv("MOTION_GATE_ENABLED"), True),
        motion_threshold=min(
            1.0, max(0.0, _parse_float(os.getenv("MOTION_THRESHOLD"), 0.005))
        ),
        motion_var_threshold=max(
            1.0, _parse_float(os.getenv("MOTION_VAR_THRESHOLD"), 25.0)
        ),
        motion_history=max(10, _parse_int(os.getenv("MOTION_HISTORY"), 300)),
        motion_downscale_width=max(
            64, _parse_int(os.getenv("MOTION_DOWNSCALE_WIDTH"), 320)
        ),
        motion_hold_seconds=max(
            0.0, _parse_float(os.getenv("MOTION_HOLD_SECONDS"), 0.5)
        ),
        motion_warmup_frames=max(
            0, _parse_int(os.getenv("MOTION_WARMUP_FRAMES"), 2)
        ),
        db_path=db_path,
        snapshots_dir=snapshots_dir,
        snapshot_max_width=max(
            64, _parse_int(os.getenv("SNAPSHOT_MAX_WIDTH"), 480)
        ),
        snapshot_quality=min(
            100, max(10, _parse_int(os.getenv("SNAPSHOT_QUALITY"), 80))
        ),
        event_track_cooldown_seconds=max(
            0.0, _parse_float(os.getenv("EVENT_TRACK_COOLDOWN_SECONDS"), 60.0)
        ),
        event_zone_cooldown_seconds=max(
            0.0, _parse_float(os.getenv("EVENT_ZONE_COOLDOWN_SECONDS"), 60.0)
        ),
        event_absence_reset_seconds=max(
            0.0, _parse_float(os.getenv("EVENT_ABSENCE_RESET_SECONDS"), 5.0)
        ),
        retention_days=max(1, _parse_int(os.getenv("RETENTION_DAYS"), 30)),
        retention_run_hour=min(
            23, max(0, _parse_int(os.getenv("RETENTION_RUN_HOUR"), 2))
        ),
        retention_run_minute=min(
            59, max(0, _parse_int(os.getenv("RETENTION_RUN_MINUTE"), 0))
        ),
        face_yunet_model=face_yunet_model,
        face_sface_model=face_sface_model,
        faces_data_dir=faces_data_dir,
        face_match_threshold=min(
            1.0, max(-1.0, _parse_float(os.getenv("FACE_MATCH_THRESHOLD"), 0.40))
        ),
        face_min_size_px=max(20, _parse_int(os.getenv("FACE_MIN_SIZE_PX"), 80)),
        face_blur_min_laplacian=max(
            0.0, _parse_float(os.getenv("FACE_BLUR_MIN_LAPLACIAN"), 60.0)
        ),
        face_min_person_crop_width=max(
            20, _parse_int(os.getenv("FACE_MIN_PERSON_CROP_WIDTH"), 80)
        ),
        face_min_person_crop_height=max(
            20, _parse_int(os.getenv("FACE_MIN_PERSON_CROP_HEIGHT"), 120)
        ),
        face_max_frames_per_track=max(
            1, _parse_int(os.getenv("FACE_MAX_FRAMES_PER_TRACK"), 5)
        ),
        face_vote_min_samples=max(
            1, _parse_int(os.getenv("FACE_VOTE_MIN_SAMPLES"), 2)
        ),
        camera_name=os.getenv("CAMERA_NAME", "Front Camera").strip() or "Front Camera",
        telegram_enabled=_parse_bool(os.getenv("TELEGRAM_ENABLED"), True),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        alert_on_unknown_person=_parse_bool(
            os.getenv("ALERT_ON_UNKNOWN_PERSON"), True
        ),
        alert_on_known_person=_parse_bool(
            os.getenv("ALERT_ON_KNOWN_PERSON"), False
        ),
        alert_on_zone_intrusion=_parse_bool(
            os.getenv("ALERT_ON_ZONE_INTRUSION"), True
        ),
        alert_on_camera_offline=_parse_bool(
            os.getenv("ALERT_ON_CAMERA_OFFLINE"), True
        ),
        alert_on_camera_online=_parse_bool(
            os.getenv("ALERT_ON_CAMERA_ONLINE"), True
        ),
        alert_on_system_error=_parse_bool(
            os.getenv("ALERT_ON_SYSTEM_ERROR"), True
        ),
        telegram_poll_interval=max(
            0.1, _parse_float(os.getenv("TELEGRAM_POLL_INTERVAL"), 2.0)
        ),
        telegram_backoff_initial=max(
            0.1, _parse_float(os.getenv("TELEGRAM_BACKOFF_INITIAL"), 2.0)
        ),
        telegram_backoff_max=max(
            0.5, _parse_float(os.getenv("TELEGRAM_BACKOFF_MAX"), 60.0)
        ),
        telegram_rate_limit_per_min=max(
            1, _parse_int(os.getenv("TELEGRAM_RATE_LIMIT_PER_MIN"), 20)
        ),
        telegram_min_interval_seconds=max(
            0.0, _parse_float(os.getenv("TELEGRAM_MIN_INTERVAL_SECONDS"), 0.5)
        ),
        api_host=os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1",
        api_port=max(1, min(65535, _parse_int(os.getenv("API_PORT"), 8000))),
        jwt_secret_key=os.getenv("JWT_SECRET_KEY", "").strip(),
        jwt_access_ttl_minutes=max(
            1, _parse_int(os.getenv("JWT_ACCESS_TTL_MINUTES"), 15)
        ),
        jwt_refresh_ttl_days=max(
            1, _parse_int(os.getenv("JWT_REFRESH_TTL_DAYS"), 7)
        ),
        login_rate_limit_max_attempts=max(
            1, _parse_int(os.getenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS"), 5)
        ),
        login_rate_limit_window_seconds=max(
            1, _parse_int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS"), 60)
        ),
        api_rate_limit_max_requests=max(
            1, _parse_int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS"), 120)
        ),
        api_write_rate_limit_max_requests=max(
            1, _parse_int(os.getenv("API_WRITE_RATE_LIMIT_MAX_REQUESTS"), 30)
        ),
        api_rate_limit_window_seconds=max(
            1, _parse_int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS"), 60)
        ),
        api_max_request_bytes=max(
            4096, _parse_int(os.getenv("API_MAX_REQUEST_BYTES"), 1024 * 1024)
        ),
        cors_allowed_origins=tuple(
            o.strip()
            for o in os.getenv(
                "CORS_ALLOWED_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if o.strip()
        ),
        upload_max_bytes=max(
            1024, _parse_int(os.getenv("UPLOAD_MAX_BYTES"), 5 * 1024 * 1024)
        ),
        cloud_sync=_parse_bool(os.getenv("CLOUD_SYNC"), False),
        supabase_url=os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        supabase_bucket=os.getenv("SUPABASE_BUCKET", "event-thumbnails").strip()
        or "event-thumbnails",
        supabase_table=os.getenv("SUPABASE_TABLE", "events").strip() or "events",
        cloud_sync_retention_days=max(
            1, _parse_int(os.getenv("CLOUD_SYNC_RETENTION_DAYS"), 30)
        ),
        cloud_sync_poll_interval=max(
            0.2, _parse_float(os.getenv("CLOUD_SYNC_POLL_INTERVAL"), 5.0)
        ),
        cloud_sync_backoff_initial=max(
            0.1, _parse_float(os.getenv("CLOUD_SYNC_BACKOFF_INITIAL"), 2.0)
        ),
        cloud_sync_backoff_max=max(
            0.5, _parse_float(os.getenv("CLOUD_SYNC_BACKOFF_MAX"), 60.0)
        ),
        cloud_sync_cleanup_hour=max(
            0, min(23, _parse_int(os.getenv("CLOUD_SYNC_CLEANUP_HOUR"), 3))
        ),
        cloud_sync_thumbnail_max_width=max(
            64, min(640, _parse_int(os.getenv("CLOUD_SYNC_THUMBNAIL_MAX_WIDTH"), 320))
        ),
        cloud_sync_thumbnail_quality=max(
            30, min(95, _parse_int(os.getenv("CLOUD_SYNC_THUMBNAIL_QUALITY"), 70))
        ),
        engine_frame_stall_seconds=max(
            5.0, _parse_float(os.getenv("ENGINE_FRAME_STALL_SECONDS"), 30.0)
        ),
        disk_low_warn_mb=max(
            64, _parse_int(os.getenv("DISK_LOW_WARN_MB"), 2048)
        ),
        disk_critical_stop_mb=max(
            32, _parse_int(os.getenv("DISK_CRITICAL_STOP_MB"), 512)
        ),
        disk_check_interval_seconds=max(
            1.0, _parse_float(os.getenv("DISK_CHECK_INTERVAL_SECONDS"), 30.0)
        ),
        backup_dir=_resolve_rel("BACKUP_DIR", "backups"),
        backup_keep_count=max(1, _parse_int(os.getenv("BACKUP_KEEP_COUNT"), 8)),
    )


def get_settings() -> Settings:
    """Convenience wrapper returning fresh settings from environment."""
    return load_settings()
