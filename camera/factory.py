"""Camera factory dispatching unified camera source instances based on configuration and URI patterns."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from camera.base import CameraSource
from camera.file_source import FileSource
from camera.url_stream import URLStreamSource
from camera.webcam import WebcamSource
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def build_rtsp_url(
    ip_or_settings: Any,
    username: str = "",
    password: str = "",
    stream: str = "stream1",
    port: int = 554,
) -> str:
    """Construct an authenticated RTSP URL from individual settings or parameters."""
    if isinstance(ip_or_settings, Settings):
        cfg = ip_or_settings
        clean_ip = (cfg.camera_ip or "").strip()
        port = int(cfg.camera_port or 554)
        clean_stream = (stream or cfg.camera_mainstream_path or "stream1").strip().lstrip("/")
        user = cfg.camera_user or ""
        pwd = cfg.camera_password or ""
    else:
        clean_ip = str(ip_or_settings or "").strip()
        clean_stream = str(stream or "stream1").strip().lstrip("/")
        user = username
        pwd = password

    if not clean_ip:
        raise ValueError("CAMERA_IP must not be empty when building an RTSP URL.")

    if user and pwd:
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    elif user:
        auth = f"{quote(user, safe='')}@"
    else:
        auth = ""

    return f"rtsp://{auth}{clean_ip}:{port}/{clean_stream}"


def create_camera_source(
    settings: Optional[Settings] = None,
    *,
    source: Optional[str] = None,
    index: Optional[int] = None,
    backend: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    fps: Optional[int] = None,
    downscale_width: Optional[int] = None,
    stale_timeout: Optional[float] = None,
    capture_factory: Optional[Callable[..., Any]] = None,
    on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    **extra_kwargs: Any,
) -> CameraSource:
    """Instantiate and configure the appropriate CameraSource (Webcam, URL/RTSP, or File)."""
    cfg = settings or get_settings()

    active_source = (source or cfg.camera_source or "").strip()
    ds_width = downscale_width or getattr(cfg, "ai_downscale_width", 640)
    timeout = stale_timeout if stale_timeout is not None else getattr(cfg, "rtsp_stale_timeout", 8.0)
    reconnect_init = getattr(cfg, "rtsp_reconnect_initial_delay", 1.0)
    reconnect_max = getattr(cfg, "rtsp_reconnect_max_delay", 30.0)

    # 1. Video File Source
    if (
        active_source.lower() == "file"
        or active_source.lower().endswith((".mp4", ".avi", ".mkv", ".mov", ".m4v"))
    ):
        file_target = (
            active_source
            if active_source.lower().endswith((".mp4", ".avi", ".mkv", ".mov", ".m4v"))
            else getattr(cfg, "camera_file_path", "test_clips/sample.mp4")
        )
        logger.info("Factory selected FileSource: %s", file_target)
        return FileSource(
            file_path=file_target,
            downscale_width=ds_width,
            capture_factory=capture_factory,
            on_event=on_event,
        )

    # 2. URL / RTSP Network Stream Source
    if (
        active_source.lower().startswith(("rtsp://", "http://", "https://"))
        or active_source.lower() in ("rtsp", "url")
    ):
        if active_source.lower() in ("rtsp", "url"):
            stream_url = build_rtsp_url(cfg) or getattr(cfg, "camera_url", "")
        else:
            stream_url = active_source

        logger.info("Factory selected URLStreamSource: %s", stream_url)
        return URLStreamSource(
            url=stream_url,
            downscale_width=ds_width,
            stale_timeout=timeout,
            reconnect_initial_delay=reconnect_init,
            reconnect_max_delay=reconnect_max,
            capture_factory=capture_factory,
            on_event=on_event,
        )

    # 3. If camera_ip is set and source is empty/default, use RTSP
    if not active_source and cfg.camera_ip:
        stream_url = build_rtsp_url(cfg)
        logger.info("Factory selected URLStreamSource from camera_ip: %s", stream_url)
        return URLStreamSource(
            url=stream_url,
            downscale_width=ds_width,
            stale_timeout=timeout,
            reconnect_initial_delay=reconnect_init,
            reconnect_max_delay=reconnect_max,
            capture_factory=capture_factory,
            on_event=on_event,
        )

    # 4. Local Webcam Source (Default)
    cam_index = index if index is not None else getattr(cfg, "webcam_index", 0)
    # Parse "webcam:1" if specified
    if active_source.lower().startswith("webcam:"):
        try:
            cam_index = int(active_source.split(":", 1)[1])
        except ValueError:
            pass

    cam_backend = backend or getattr(cfg, "webcam_backend", "dshow")
    cam_width = width if width is not None else getattr(cfg, "camera_width", 640)
    cam_height = height if height is not None else getattr(cfg, "camera_height", 480)
    cam_fps = fps if fps is not None else getattr(cfg, "camera_fps", 30)

    logger.info("Factory selected WebcamSource: index=%d, backend=%s", cam_index, cam_backend)
    return WebcamSource(
        index=cam_index,
        backend=cam_backend,
        width=cam_width,
        height=cam_height,
        fps=cam_fps,
        downscale_width=ds_width,
        stale_timeout=timeout,
        capture_factory=capture_factory,
        on_event=on_event,
    )
