"""Backward-compatibility adapter delegating to the unified modular camera package."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional
from urllib.parse import quote

import numpy as np

from camera.base import CameraSource, CameraStatus, FramePacket
from camera.factory import create_camera_source
from camera.file_source import FileSource
from camera.url_stream import ensure_rtsp_tcp_transport
from camera.webcam import WebcamSource, default_capture_factory, parse_webcam_source
from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def build_rtsp_url(
    ip: str,
    username: str = "",
    password: str = "",
    stream: str = "stream1",
    port: int = 554,
) -> str:
    """Construct an RTSP URL with percent-encoded credentials."""
    clean_ip = (ip or "").strip()
    if not clean_ip:
        raise ValueError("CAMERA_IP must not be empty when building an RTSP URL.")

    clean_stream = (stream or "stream1").strip().lstrip("/")
    if username and password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    elif username:
        auth = f"{quote(username, safe='')}@"
    else:
        auth = ""

    return f"rtsp://{auth}{clean_ip}:{port}/{clean_stream}"


class RTSPCamera(CameraSource):
    """Compatibility wrapper providing the legacy RTSPCamera interface over the new camera layer."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        stream: str = "sub",
        source: Optional[Any] = None,
        stale_timeout: Optional[float] = None,
        reconnect_initial_delay: Optional[float] = None,
        reconnect_max_delay: Optional[float] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        capture_factory: Optional[Callable[..., Any]] = None,
        downscale_width: int = 640,
        **kwargs: Any,
    ) -> None:
        super().__init__(downscale_width=downscale_width)
        self.settings = settings or get_settings()
        self._stream_profile = stream
        self._custom_source = source
        self._legacy_on_event = on_event
        self._legacy_capture_factory = capture_factory

        # URL construction
        self.main_url = ""
        self.sub_url = ""
        if self.settings.camera_ip:
            cam_pass = getattr(self.settings, "camera_pass", "") or getattr(self.settings, "camera_password", "")
            main_stream = getattr(self.settings, "camera_main_stream", "stream1") or getattr(self.settings, "camera_mainstream_path", "stream1")
            sub_stream = getattr(self.settings, "camera_sub_stream", "stream2") or getattr(self.settings, "camera_substream_path", "stream2")
            self.main_url = build_rtsp_url(
                ip=self.settings.camera_ip,
                username=self.settings.camera_user or "",
                password=cam_pass,
                stream=main_stream,
                port=self.settings.camera_port or 554,
            )
            self.sub_url = build_rtsp_url(
                ip=self.settings.camera_ip,
                username=self.settings.camera_user or "",
                password=cam_pass,
                stream=sub_stream,
                port=self.settings.camera_port or 554,
            )

        if self._custom_source is not None:
            self.active_source = str(self._custom_source)
        elif self._stream_profile == "main" and self.main_url:
            self.active_source = self.main_url
        elif self.sub_url:
            self.active_source = self.sub_url
        else:
            self.active_source = str(self.settings.camera_source or "webcam")

        self.is_rtsp = str(self.active_source).lower().startswith("rtsp://")

        # Map new events (camera_online, camera_offline) to legacy event names (connected, disconnected)
        def _mapped_on_event(name: str, payload: dict[str, Any]) -> None:
            if not on_event:
                return
            legacy_name = "connected" if name == "camera_online" else "disconnected" if name == "camera_offline" else name
            on_event(legacy_name, payload)

        # Adapt capture factory if provided with (src, use_rtsp) signature
        adapted_factory = None
        if capture_factory is not None:
            def _adapted_factory(*args: Any, **kwargs: Any) -> Any:
                target_src = args[0] if args else self.active_source
                try:
                    return capture_factory(target_src, self.is_rtsp)
                except TypeError:
                    return capture_factory(*args, **kwargs)
            adapted_factory = _adapted_factory

        # Create underlying source
        timeout = stale_timeout if stale_timeout is not None else self.settings.rtsp_stale_timeout
        reconnect_init = reconnect_initial_delay if reconnect_initial_delay is not None else self.settings.rtsp_reconnect_initial_delay
        reconnect_max = reconnect_max_delay if reconnect_max_delay is not None else self.settings.rtsp_reconnect_max_delay
        self._underlying = create_camera_source(
            self.settings,
            source=self.active_source,
            stale_timeout=timeout,
            reconnect_initial_delay=reconnect_init,
            reconnect_max_delay=reconnect_max,
            capture_factory=adapted_factory,
            on_event=_mapped_on_event if on_event else None,
            downscale_width=downscale_width,
        )
        self._underlying.on_status_change(self._on_status_change)

    def _on_status_change(self, status: CameraStatus, metadata: dict[str, Any]) -> None:
        self._set_status(status, metadata)

    @property
    def masked_source(self) -> str:
        return self._underlying.masked_source

    @property
    def is_connected(self) -> bool:
        return self._underlying.is_connected

    @property
    def _is_webcam(self) -> bool:
        return isinstance(self._underlying, WebcamSource) or parse_webcam_source(self.active_source) is not None

    @property
    def is_webcam(self) -> bool:
        return self._is_webcam

    @property
    def _is_file_source(self) -> bool:
        return isinstance(self._underlying, FileSource)

    @property
    def is_file_source(self) -> bool:
        return self._is_file_source

    @property
    def status(self) -> CameraStatus:
        return self._underlying.status

    @property
    def fps(self) -> float:
        return self._underlying.fps

    @property
    def frame_count(self) -> int:
        return self._underlying.frame_count

    def get_latest_frame(self, copy: bool = True) -> tuple[bool, Optional[FramePacket]]:
        return self._underlying.get_latest_frame(copy=copy)

    def read(self, copy: bool = True) -> tuple[bool, Optional[np.ndarray]]:
        return self._underlying.read(copy=copy)

    def read_with_seq(self, copy: bool = True) -> tuple[bool, Optional[np.ndarray], int]:
        return self._underlying.read_with_seq(copy=copy)

    def start(self) -> RTSPCamera:
        self._underlying.start()
        return self

    def stop(self) -> None:
        self._underlying.stop()
        if self._legacy_on_event:
            try:
                self._legacy_on_event("disconnected", {"event": "disconnected", "reason": "stopped", "source": self.masked_source})
            except Exception:
                pass


__all__ = [
    "RTSPCamera",
    "build_rtsp_url",
    "ensure_rtsp_tcp_transport",
    "parse_webcam_source",
    "default_capture_factory",
]
