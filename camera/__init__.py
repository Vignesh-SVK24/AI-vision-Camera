"""Unified camera capture package providing abstract source interface, watchdog, and factory."""

from camera.base import (
    CameraSource,
    CameraStatus,
    FramePacket,
    downscale_frame,
    mask_sensitive_text,
    mask_url_credentials,
)
from camera.factory import build_rtsp_url, create_camera_source
from camera.file_source import FileSource
from camera.health import CameraHealthWatchdog, ReconnectBackoff
from camera.url_stream import URLStreamSource, ensure_rtsp_tcp_transport
from camera.webcam import WebcamSource, default_capture_factory, parse_webcam_source

from camera.rtsp_client import RTSPCamera

__all__ = [
    "CameraSource",
    "CameraStatus",
    "FramePacket",
    "WebcamSource",
    "URLStreamSource",
    "FileSource",
    "create_camera_source",
    "CameraHealthWatchdog",
    "ReconnectBackoff",
    "mask_url_credentials",
    "mask_sensitive_text",
    "downscale_frame",
    "ensure_rtsp_tcp_transport",
    "build_rtsp_url",
    "parse_webcam_source",
    "default_capture_factory",
    "RTSPCamera",
]
