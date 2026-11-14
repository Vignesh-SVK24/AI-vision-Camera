"""FastAPI local-first REST & WebSocket backend sharing SQLite WAL with the engine."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.auth import AuthService, verify_password
from backend.live_stream import LiveCameraManager
from backend.schemas import (
    AuditLogItem,
    CameraStatusResponse,
    DeletePersonResponse,
    EventItem,
    EventListResponse,
    HealthResponse,
    LoginRequest,
    NotificationItem,
    PersonEnrollResponse,
    PersonItem,
    RefreshRequest,
    SettingsResponse,
    SettingsUpdateRequest,
    TokenResponse,
    UserResponse,
    ZonePayload,
    ZonesResponse,
    ZonesUpdateRequest,
)
from backend.security import (
    ConsistentRateLimitMiddleware,
    LoginRateLimiter,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    validate_bind_host,
    validate_image_upload,
)
from config.logging_config import setup_logging
from config.settings import BASE_DIR, Settings, get_settings
from faces.enrollment import ConsentRecord, EnrollmentError, add_person, delete_person
from faces.face_engine import FaceEngine
from storage.db import Database
from storage.snapshots import resolve_snapshot_path
from tracking.zones import DEFAULT_ZONES_PATH, Zone, load_zones, save_zones

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

ALLOWED_SETTING_KEYS = frozenset(
    {
        "camera_name",
        "detection_confidence",
        "detection_target_fps",
        "face_match_threshold",
        "event_track_cooldown_seconds",
        "event_zone_cooldown_seconds",
        "retention_days",
        "alert_on_unknown_person",
        "alert_on_known_person",
        "alert_on_zone_intrusion",
        "alert_on_camera_offline",
        "alert_on_camera_online",
        "alert_on_system_error",
    }
)


def _parse_date_filter(raw: Optional[str], *, end_of_day: bool = False) -> Optional[float]:
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    try:
        if len(text) == 10:  # YYYY-MM-DD
            dt = datetime.fromisoformat(text)
            if end_of_day:
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date/datetime filter '{raw}'. Use ISO-8601 or YYYY-MM-DD.",
        ) from exc


def _row_to_event_item(row: dict[str, Any]) -> EventItem:
    meta_raw = row.get("metadata_json")
    metadata = None
    if isinstance(meta_raw, str) and meta_raw.strip():
        try:
            parsed = json.loads(meta_raw)
            if isinstance(parsed, dict):
                metadata = parsed
        except json.JSONDecodeError:
            metadata = None

    event_id = int(row["id"])
    snap_url = f"/snapshots/{event_id}" if row.get("snapshot_path") else None

    return EventItem(
        id=event_id,
        event_type=str(row["event_type"]),
        created_at=str(row["created_at"]),
        track_id=row.get("track_id"),
        zone_id=row.get("zone_id"),
        zone_name=row.get("zone_name"),
        person_id=row.get("person_id"),
        person_name=row.get("person_name"),
        confidence=row.get("confidence"),
        snapshot_url=snap_url,
        message=row.get("message"),
        metadata=metadata,
    )


def create_app(
    settings: Optional[Settings] = None,
    *,
    db: Optional[Database] = None,
    face_engine: Optional[FaceEngine] = None,
    live_manager: Optional[LiveCameraManager] = None,
    zones_path: Path | str = DEFAULT_ZONES_PATH,
    ws_poll_interval: float = 0.5,
) -> FastAPI:
    """Build and configure the FastAPI application."""
    cfg = settings or get_settings()
    owns_db = db is None
    database = db or Database(settings=cfg)
    owns_live_manager = live_manager is None
    active_live_manager = live_manager or LiveCameraManager(cfg)
    auth_service = AuthService(cfg)
    rate_limiter = LoginRateLimiter(
        max_attempts=cfg.login_rate_limit_max_attempts,
        window_seconds=cfg.login_rate_limit_window_seconds,
    )
    resolved_zones_path = Path(zones_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owns_live_manager:
                active_live_manager.close()
            if owns_db:
                database.close()

    app = FastAPI(
        title=f"{cfg.app_name} API",
        version="1.0.0",
        description="Local-first REST and WebSocket API for the surveillance dashboard.",
        lifespan=lifespan,
    )

    app.add_middleware(
        RequestSizeLimitMiddleware,
        default_max_bytes=cfg.api_max_request_bytes,
        upload_max_bytes=max(cfg.api_max_request_bytes, cfg.upload_max_bytes * 10 + 1024 * 1024),
        upload_paths=("/people",),
    )
    app.add_middleware(
        ConsistentRateLimitMiddleware,
        read_max_requests=cfg.api_rate_limit_max_requests,
        write_max_requests=cfg.api_write_rate_limit_max_requests,
        auth_max_requests=max(10, cfg.login_rate_limit_max_attempts * 2),
        window_seconds=cfg.api_rate_limit_window_seconds,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.state.settings = cfg
    app.state.db = database
    app.state.auth = auth_service
    app.state.live_camera = active_live_manager

    def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ) -> dict[str, Any]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = auth_service.decode_token(
            credentials.credentials,
            expected_type="access",
        )
        user_id = int(payload["sub"])
        user = database.get_user_by_id(user_id)
        if user is None or not int(user.get("is_active", 0)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Account is inactive or no longer exists.",
            )
        return user

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            db="ok",
            schema_version=database.schema_version(),
        )

    @app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
    def login(body: LoginRequest, request: Request) -> TokenResponse:
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"{client_ip}:{body.username.strip().lower()}"
        allowed, retry_after = rate_limiter.check_allowed(rate_key)
        if not allowed:
            database.record_audit(
                actor=body.username.strip(),
                action="login_rate_limited",
                target_type="auth",
                details={"client_ip": client_ip, "retry_after": retry_after},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

        user = database.get_user_by_username(body.username)
        if (
            user is None
            or not int(user.get("is_active", 0))
            or not verify_password(user["password_hash"], body.password)
        ):
            rate_limiter.record_failure(rate_key)
            database.record_audit(
                actor=body.username.strip(),
                action="login_failed",
                target_type="auth",
                details={"client_ip": client_ip},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            )

        rate_limiter.reset(rate_key)
        database.record_audit(
            actor=str(user["username"]),
            action="login_success",
            target_type="user",
            target_id=str(user["id"]),
            details={"client_ip": client_ip},
        )
        return TokenResponse(
            access_token=auth_service.create_access_token(user),
            refresh_token=auth_service.create_refresh_token(user),
        )

    @app.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
    def refresh_token(body: RefreshRequest) -> TokenResponse:
        payload = auth_service.decode_token(body.refresh_token, expected_type="refresh")
        user = database.get_user_by_id(int(payload["sub"]))
        if user is None or not int(user.get("is_active", 0)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account not available.",
            )
        database.record_audit(
            actor=str(user["username"]),
            action="token_refresh",
            target_type="user",
            target_id=str(user["id"]),
        )
        return TokenResponse(
            access_token=auth_service.create_access_token(user),
            refresh_token=auth_service.create_refresh_token(user),
        )

    @app.get("/auth/me", response_model=UserResponse, tags=["auth"])
    def auth_me(user: dict[str, Any] = Depends(get_current_user)) -> UserResponse:
        return UserResponse(
            id=int(user["id"]),
            username=str(user["username"]),
            role=str(user["role"]),
            is_active=bool(user["is_active"]),
        )

    @app.get("/cameras/status", response_model=CameraStatusResponse, tags=["cameras"])
    def camera_status(_: dict[str, Any] = Depends(get_current_user)) -> CameraStatusResponse:
        info = database.get_latest_camera_status()
        live_state = active_live_manager.get_state()
        status_value = (
            "online"
            if live_state.get("status") == "connected"
            else info["status"]
        )
        return CameraStatusResponse(
            camera_name=cfg.camera_name,
            status=status_value,
            last_status_change=info["last_status_change"],
            last_event_at=info["last_event_at"],
            last_event_type=info["last_event_type"],
            total_events=info["total_events"],
        )

    @app.get("/cameras/live/snapshot", tags=["cameras"])
    def camera_live_snapshot(
        _: dict[str, Any] = Depends(get_current_user),
    ) -> Response:
        try:
            jpeg_bytes = active_live_manager.grab_snapshot_jpeg(wait_seconds=10.0)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        if jpeg_bytes is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Camera stream did not return a frame yet. Check camera permissions or try again.",
            )
        return Response(
            content=jpeg_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/events", response_model=EventListResponse, tags=["events"])
    def list_events(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        event_type: Optional[str] = Query(default=None, alias="type"),
        person_id: Optional[int] = Query(default=None),
        person_name: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: dict[str, Any] = Depends(get_current_user),
    ) -> EventListResponse:
        start_ts = _parse_date_filter(start_date, end_of_day=False)
        end_ts = _parse_date_filter(end_date, end_of_day=True)
        rows = database.query_events(
            start_ts=start_ts,
            end_ts=end_ts,
            event_type=event_type,
            person_id=person_id,
            person_name=person_name,
            limit=limit,
            offset=offset,
        )
        items = [_row_to_event_item(r) for r in rows]
        return EventListResponse(events=items, count=len(items))

    @app.get("/snapshots/{event_id}", tags=["events"])
    def get_snapshot(
        event_id: int,
        _: dict[str, Any] = Depends(get_current_user),
    ) -> FileResponse:
        event = database.get_event(event_id)
        if event is None or not event.get("snapshot_path"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Snapshot not found.",
            )

        resolved = resolve_snapshot_path(str(event["snapshot_path"]), base_dir=BASE_DIR).resolve()
        snapshots_root = cfg.snapshots_dir.resolve()
        try:
            resolved.relative_to(snapshots_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Snapshot path outside configured snapshots directory.",
            ) from exc

        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Snapshot file no longer exists on disk.",
            )
        return FileResponse(path=str(resolved), media_type="image/jpeg")

    @app.get("/people", response_model=list[PersonItem], tags=["people"])
    def get_people(_: dict[str, Any] = Depends(get_current_user)) -> list[PersonItem]:
        rows = database.list_people()
        return [
            PersonItem(
                id=int(r["id"]),
                name=str(r["name"]),
                is_trusted=bool(r["is_trusted"]),
                consent_date=r.get("consent_date"),
                consent_note=r.get("consent_note"),
                embedding_count=int(r.get("embedding_count", 0)),
                photo_count=int(r.get("photo_count", 0)),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    @app.post(
        "/people",
        response_model=PersonEnrollResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["people"],
    )
    async def create_person(
        name: str = Form(..., min_length=1, max_length=100),
        consent_date: str = Form(..., min_length=10, max_length=32),
        consent_note: str = Form(..., min_length=1, max_length=256),
        files: list[UploadFile] = File(...),
        user: dict[str, Any] = Depends(get_current_user),
    ) -> PersonEnrollResponse:
        if not files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one photo upload is required.",
            )

        decoded_images = []
        for upload in files:
            data = await upload.read()
            img = validate_image_upload(
                filename=upload.filename or "",
                content_type=upload.content_type,
                data=data,
                max_bytes=cfg.upload_max_bytes,
            )
            decoded_images.append(img)

        try:
            result = add_person(
                database,
                name=name,
                images=decoded_images,
                consent=ConsentRecord(date=consent_date, note=consent_note),
                face_engine=face_engine,
                settings=cfg,
            )
        except EnrollmentError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        database.record_audit(
            actor=str(user["username"]),
            action="api_enroll_person",
            target_type="person",
            target_id=str(result.person_id),
            details={"name": result.name, "embeddings": result.enrolled_embeddings},
        )
        return PersonEnrollResponse(
            person_id=result.person_id,
            name=result.name,
            enrolled_embeddings=result.enrolled_embeddings,
            rejected_reasons=result.rejected_reasons,
        )

    @app.delete("/people/{person_id}", response_model=DeletePersonResponse, tags=["people"])
    def remove_person(
        person_id: int,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> DeletePersonResponse:
        removed = delete_person(database, person_id, settings=cfg)
        if removed == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Person {person_id} not found.",
            )
        database.record_audit(
            actor=str(user["username"]),
            action="api_delete_person",
            target_type="person",
            target_id=str(person_id),
        )
        return DeletePersonResponse(deleted=True, person_id=person_id)

    @app.get("/zones", response_model=ZonesResponse, tags=["zones"])
    def get_zones(_: dict[str, Any] = Depends(get_current_user)) -> ZonesResponse:
        loaded = load_zones(resolved_zones_path)
        return ZonesResponse(
            zones=[
                ZonePayload(
                    id=z.zone_id,
                    name=z.name,
                    enabled=z.enabled,
                    dwell_seconds=z.dwell_seconds,
                    points=[[int(x), int(y)] for x, y in z.points],
                    color=[int(c) for c in z.color],
                )
                for z in loaded
            ]
        )

    @app.put("/zones", response_model=ZonesResponse, tags=["zones"])
    def update_zones(
        body: ZonesUpdateRequest,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> ZonesResponse:
        zone_objs = [
            Zone(
                zone_id=z.id,
                name=z.name,
                enabled=z.enabled,
                dwell_seconds=z.dwell_seconds,
                points=[(int(p[0]), int(p[1])) for p in z.points],
                color=(int(z.color[0]), int(z.color[1]), int(z.color[2])),
            )
            for z in body.zones
        ]
        save_zones(zone_objs, resolved_zones_path)
        database.record_audit(
            actor=str(user["username"]),
            action="api_update_zones",
            target_type="zones",
            details={"zone_count": len(zone_objs)},
        )
        return ZonesResponse(zones=body.zones)

    @app.get("/settings", response_model=SettingsResponse, tags=["settings"])
    def get_api_settings(_: dict[str, Any] = Depends(get_current_user)) -> SettingsResponse:
        return SettingsResponse(
            app_name=cfg.app_name,
            camera_name=cfg.camera_name,
            detection_confidence=cfg.detection_confidence,
            detection_imgsz=cfg.detection_imgsz,
            detection_target_fps=cfg.detection_target_fps,
            motion_gate_enabled=cfg.motion_gate_enabled,
            face_match_threshold=cfg.face_match_threshold,
            event_track_cooldown_seconds=cfg.event_track_cooldown_seconds,
            event_zone_cooldown_seconds=cfg.event_zone_cooldown_seconds,
            retention_days=cfg.retention_days,
            telegram_enabled=cfg.telegram_enabled,
            telegram_configured=bool(cfg.telegram_bot_token and cfg.telegram_chat_id),
            alert_on_unknown_person=cfg.alert_on_unknown_person,
            alert_on_known_person=cfg.alert_on_known_person,
            alert_on_zone_intrusion=cfg.alert_on_zone_intrusion,
            overrides=database.list_settings(),
        )

    @app.put("/settings", response_model=SettingsResponse, tags=["settings"])
    def update_api_settings(
        body: SettingsUpdateRequest,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> SettingsResponse:
        disallowed = set(body.overrides.keys()) - ALLOWED_SETTING_KEYS
        if disallowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported or read-only setting keys: {sorted(disallowed)}",
            )

        for key, value in body.overrides.items():
            database.set_setting(key, str(value))

        database.record_audit(
            actor=str(user["username"]),
            action="api_update_settings",
            target_type="settings",
            details={"keys": sorted(body.overrides.keys())},
        )
        return get_api_settings(user)

    @app.get("/notifications", response_model=list[NotificationItem], tags=["notifications"])
    def get_notifications(
        limit: int = Query(default=50, ge=1, le=500),
        _: dict[str, Any] = Depends(get_current_user),
    ) -> list[NotificationItem]:
        rows = database.list_outbox_notifications(limit=limit)
        return [
            NotificationItem(
                id=int(r["id"]),
                event_id=int(r["event_id"]),
                event_type=str(r["event_type"]),
                event_created_at=str(r["event_created_at"]),
                notified=bool(r["notified"]),
                synced=bool(r["synced"]),
                notify_attempts=int(r["notify_attempts"]),
                last_error=r.get("last_error"),
                zone_name=r.get("zone_name"),
                message=r.get("message"),
            )
            for r in rows
        ]

    @app.get("/audit-logs", response_model=list[AuditLogItem], tags=["security"])
    def get_audit_logs(
        limit: int = Query(default=50, ge=1, le=500),
        _: dict[str, Any] = Depends(get_current_user),
    ) -> list[AuditLogItem]:
        rows = database.list_audit_logs(limit=limit)
        return [
            AuditLogItem(
                id=int(r["id"]),
                actor=str(r["actor"]),
                action=str(r["action"]),
                target_type=r.get("target_type"),
                target_id=r.get("target_id"),
                details_json=r.get("details_json"),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    async def _authenticate_ws(
        websocket: WebSocket,
        token: Optional[str],
    ) -> Optional[dict[str, Any]]:
        raw_token = token
        if not raw_token:
            auth_header = websocket.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                raw_token = auth_header[7:].strip()

        if not raw_token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None

        try:
            payload = auth_service.decode_token(raw_token, expected_type="access")
            user = database.get_user_by_id(int(payload["sub"]))
            if user is None or not int(user.get("is_active", 0)):
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return None
            return user
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None

    @app.websocket("/ws/events")
    async def ws_events(
        websocket: WebSocket,
        token: Optional[str] = Query(default=None),
    ) -> None:
        user = await _authenticate_ws(websocket, token)
        if user is None:
            return

        await websocket.accept()
        last_id = database.get_latest_event_id()
        await websocket.send_json({"type": "connected", "last_event_id": last_id})

        try:
            while True:
                new_rows = database.get_events_since(last_id, limit=50)
                for row in new_rows:
                    item = _row_to_event_item(row)
                    last_id = item.id
                    await websocket.send_json(
                        {"type": "event", "data": item.model_dump()}
                    )
                await asyncio.sleep(ws_poll_interval)
        except WebSocketDisconnect:
            logger.debug("WebSocket client disconnected.")

    @app.websocket("/ws/live")
    async def ws_live(
        websocket: WebSocket,
        token: Optional[str] = Query(default=None),
    ) -> None:
        user = await _authenticate_ws(websocket, token)
        if user is None:
            return

        await websocket.accept()
        try:
            await asyncio.to_thread(active_live_manager.acquire)
        except Exception as exc:
            await websocket.send_json(
                {
                    "type": "status",
                    "status": "disconnected",
                    "camera_name": cfg.camera_name,
                    "fps": 0.0,
                    "error": str(exc),
                }
            )
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return

        last_seq = -1
        last_sent_status: Optional[str] = None
        last_status_tick = 0.0

        try:
            while True:
                state = active_live_manager.get_state()
                now = asyncio.get_running_loop().time()
                if state["status"] != last_sent_status or (now - last_status_tick) >= 1.0:
                    last_sent_status = str(state["status"])
                    last_status_tick = now
                    await websocket.send_json(
                        {
                            "type": "status",
                            "status": state["status"],
                            "camera_name": cfg.camera_name,
                            "fps": state["fps"],
                            "error": state.get("error"),
                        }
                    )

                jpeg_bytes, next_seq = await asyncio.to_thread(
                    active_live_manager.get_latest_jpeg,
                    last_seq,
                )
                if jpeg_bytes is not None:
                    last_seq = next_seq
                    await websocket.send_bytes(jpeg_bytes)

                await asyncio.sleep(0.065)
        except WebSocketDisconnect:
            logger.debug("Live stream WebSocket client disconnected.")
        finally:
            await asyncio.to_thread(active_live_manager.release)

    return app


def run_server() -> None:
    """Run the backend API server bound strictly to localhost or Tailscale."""
    settings = get_settings()
    setup_logging(settings)
    bind_host = validate_bind_host(settings.api_host)
    app = create_app(settings=settings)
    uvicorn.run(
        app,
        host=bind_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run_server()
