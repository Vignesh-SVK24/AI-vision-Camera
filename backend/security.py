"""Security helpers: login rate limiting and strict image upload validation."""

from __future__ import annotations

import ipaddress
import json
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from fastapi import HTTPException, status

ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})


class LoginRateLimiter:
    """Thread-safe sliding-window rate limiter for login attempts."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: int = 60,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max(1, int(max_attempts))
        self._window = max(1, int(window_seconds))
        self._monotonic = monotonic_fn
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def check_allowed(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds) for the given client key."""
        now = self._monotonic()
        with self._lock:
            dq = self._failures.get(key)
            if not dq:
                return True, 0
            self._prune(dq, now)
            if len(dq) >= self._max_attempts:
                retry_after = max(1, int(math.ceil(self._window - (now - dq[0]))))
                return False, retry_after
            return True, 0

    def record_failure(self, key: str) -> None:
        now = self._monotonic()
        with self._lock:
            dq = self._failures.setdefault(key, deque())
            self._prune(dq, now)
            dq.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _matches_image_magic_bytes(data: bytes) -> bool:
    if len(data) < 12:
        return False
    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return True
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    # WEBP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def validate_image_upload(
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
    max_bytes: int,
) -> np.ndarray:
    """Validate upload size, extension, MIME type, magic bytes, and decode image."""
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds maximum allowed size ({max_bytes} bytes).",
        )

    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{ext}'. Allowed: .jpg, .jpeg, .png, .webp.",
        )

    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported Content-Type '{mime}'.",
            )

    if not _matches_image_magic_bytes(data):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match allowed image magic bytes (JPEG/PNG/WEBP).",
        )

    arr = np.frombuffer(data, dtype=np.uint8)
    decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file could not be decoded as a valid image.",
        )

    return decoded


_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def validate_bind_host(host: str) -> str:
    """Validate that a bind host is strictly localhost or a Tailscale 100.64.0.0/10 IP."""
    cleaned = (host or "").strip()
    if not cleaned:
        raise ValueError("Bind host must not be empty.")
    if cleaned.lower() == "localhost":
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Refusing non-IP bind host '{cleaned}'. Use 127.0.0.1 or a Tailscale 100.64.0.0/10 IP."
        ) from exc

    if ip.is_loopback:
        return str(ip)
    if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILSCALE_CGNAT:
        return str(ip)

    raise ValueError(
        f"Refusing insecure bind host '{cleaned}': only loopback (127.0.0.1/::1) "
        "or Tailscale CGNAT (100.64.0.0/10) interfaces are permitted."
    )


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window request rate limiter keyed by client and tier."""

    def __init__(
        self,
        *,
        max_requests: int = 120,
        window_seconds: int = 60,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_requests = max(1, int(max_requests))
        self._window = max(1, int(window_seconds))
        self._monotonic = monotonic_fn
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._ops = 0

    def _prune(self, dq: deque[float], now: float) -> None:
        cutoff = now - self._window
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def _sweep_empty_locked(self, now: float) -> None:
        stale = []
        for k, dq in self._hits.items():
            self._prune(dq, now)
            if not dq:
                stale.append(k)
        for k in stale:
            self._hits.pop(k, None)

    def consume(
        self,
        key: str,
        *,
        limit_override: int | None = None,
    ) -> tuple[bool, int, int]:
        """Record a request if within quota. Returns (allowed, remaining, retry_after)."""
        limit = max(1, int(limit_override)) if limit_override is not None else self._max_requests
        now = self._monotonic()
        with self._lock:
            self._ops += 1
            if self._ops % 512 == 0:
                self._sweep_empty_locked(now)

            dq = self._hits.setdefault(key, deque())
            self._prune(dq, now)
            if len(dq) >= limit:
                retry_after = max(1, int(math.ceil(self._window - (now - dq[0]))))
                return False, 0, retry_after

            dq.append(now)
            remaining = max(0, limit - len(dq))
            return True, remaining, 0


async def _send_json_error(
    send: Any,
    status_code: int,
    detail: str,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    payload = json.dumps({"detail": detail}).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status_code, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class SecurityHeadersMiddleware:
    """ASGI middleware injecting defensive security headers on all HTTP responses."""

    _BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (
            b"content-security-policy",
            b"default-src 'none'; frame-ancestors 'none'",
        ),
    )

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        req_headers = dict(scope.get("headers", ()))
        proto = req_headers.get(b"x-forwarded-proto", b"").decode("latin1").lower()
        is_https = scope.get("scheme") == "https" or proto == "https"

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in self._BASE_HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                if b"cache-control" not in existing:
                    headers.append((b"cache-control", b"no-store"))
                if is_https and b"strict-transport-security" not in existing:
                    headers.append(
                        (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLargeError(Exception):
    pass


class RequestSizeLimitMiddleware:
    """ASGI middleware enforcing maximum HTTP request payload sizes."""

    def __init__(
        self,
        app: Any,
        *,
        default_max_bytes: int = 1024 * 1024,
        upload_max_bytes: int = 52 * 1024 * 1024,
        upload_paths: tuple[str, ...] = ("/people",),
    ) -> None:
        self.app = app
        self.default_max_bytes = max(1024, int(default_max_bytes))
        self.upload_max_bytes = max(self.default_max_bytes, int(upload_max_bytes))
        self.upload_paths = upload_paths

    def _limit_for_request(self, method: str, path: str) -> int:
        if method == "POST" and any(path == p or path.startswith(f"{p}/") for p in self.upload_paths):
            return self.upload_max_bytes
        return self.default_max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        max_bytes = self._limit_for_request(method, path)

        req_headers = dict(scope.get("headers", ()))
        raw_cl = req_headers.get(b"content-length")
        if raw_cl is not None:
            try:
                content_length = int(raw_cl.decode("ascii").strip())
            except ValueError:
                await _send_json_error(
                    send, status.HTTP_400_BAD_REQUEST, "Invalid Content-Length header."
                )
                return
            if content_length > max_bytes:
                await _send_json_error(
                    send,
                    getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                    f"Request body exceeds limit ({max_bytes} bytes).",
                )
                return

        seen_bytes = 0
        response_started = False

        async def guarded_receive() -> dict[str, Any]:
            nonlocal seen_bytes
            message = await receive()
            if message["type"] == "http.request":
                seen_bytes += len(message.get("body", b""))
                if seen_bytes > max_bytes:
                    raise _BodyTooLargeError()
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, tracked_send)
        except _BodyTooLargeError:
            if not response_started:
                await _send_json_error(
                    send,
                    getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                    f"Request body exceeds limit ({max_bytes} bytes).",
                )


class ConsistentRateLimitMiddleware:
    """ASGI middleware enforcing per-IP sliding-window limits across all HTTP routes."""

    def __init__(
        self,
        app: Any,
        *,
        read_max_requests: int = 120,
        write_max_requests: int = 30,
        auth_max_requests: int = 20,
        window_seconds: int = 60,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = app
        self.read_limit = max(1, int(read_max_requests))
        self.write_limit = max(1, int(write_max_requests))
        self.auth_limit = max(1, int(auth_max_requests))
        self.limiter = SlidingWindowRateLimiter(
            max_requests=self.read_limit,
            window_seconds=window_seconds,
            monotonic_fn=monotonic_fn,
        )

    def _tier(self, method: str, path: str) -> tuple[str, int]:
        if path in ("/auth/login", "/auth/refresh"):
            return "auth", self.auth_limit
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            return "write", self.write_limit
        return "read", self.read_limit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        tier, limit = self._tier(method, path)

        allowed, remaining, retry_after = self.limiter.consume(
            f"{tier}:{client_ip}",
            limit_override=limit,
        )
        if not allowed:
            await _send_json_error(
                send,
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Rate limit exceeded. Slow down and retry later.",
                extra_headers=[
                    (b"retry-after", str(retry_after).encode("ascii")),
                    (b"x-ratelimit-limit", str(limit).encode("ascii")),
                    (b"x-ratelimit-remaining", b"0"),
                ],
            )
            return

        async def send_with_quota(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-ratelimit-limit", str(limit).encode("ascii")))
                headers.append((b"x-ratelimit-remaining", str(remaining).encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_quota)
