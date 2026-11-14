"""Tests for API security headers, request size limits, consistent rate limiting, and secret scan."""

from __future__ import annotations

import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _import_security():
    if "cv2" not in sys.modules:
        try:
            importlib.import_module("cv2")
        except ImportError:
            fake_cv2 = types.ModuleType("cv2")
            fake_cv2.IMREAD_COLOR = 1
            fake_cv2.imdecode = lambda *_args, **_kwargs: None
            sys.modules["cv2"] = fake_cv2
    return importlib.import_module("backend.security")


def _drive_coro(coro: Any) -> Any:
    """Execute an async ASGI coroutine that awaits only pure-Python ASGI steps."""
    try:
        while True:
            coro.send(None)
    except StopIteration as done:
        return done.value


def _invoke_asgi(
    asgi_app: Any,
    *,
    method: str = "GET",
    path: str = "/health",
    headers: dict[str, str] | None = None,
    body_chunks: list[bytes] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    scheme: str = "http",
) -> tuple[int, dict[str, str], bytes]:
    raw_headers = [
        (k.lower().encode("latin1"), v.encode("latin1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": ("127.0.0.1", 8000),
    }

    chunks = list(body_chunks if body_chunks is not None else [b""])
    idx = 0

    async def receive() -> dict[str, Any]:
        nonlocal idx
        if idx < len(chunks):
            chunk = chunks[idx]
            idx += 1
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": idx < len(chunks),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    resp_status = 500
    resp_headers: dict[str, str] = {}
    resp_body = bytearray()

    async def send(message: dict[str, Any]) -> None:
        nonlocal resp_status, resp_headers
        if message["type"] == "http.response.start":
            resp_status = int(message["status"])
            resp_headers = {
                k.decode("latin1").lower(): v.decode("latin1")
                for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            resp_body.extend(message.get("body", b""))

    _drive_coro(asgi_app(scope, receive, send))
    return resp_status, resp_headers, bytes(resp_body)


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestSecurityHardening(unittest.TestCase):
    def test_validate_bind_host_allows_loopback_and_tailscale_only(self) -> None:
        sec = _import_security()
        self.assertEqual(sec.validate_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(sec.validate_bind_host("localhost"), "127.0.0.1")
        self.assertEqual(sec.validate_bind_host("::1"), "::1")
        self.assertEqual(sec.validate_bind_host("100.64.12.34"), "100.64.12.34")
        self.assertEqual(sec.validate_bind_host("100.127.255.254"), "100.127.255.254")

        for bad in ("0.0.0.0", "::", "192.168.1.10", "10.0.0.2", "172.16.0.5", "8.8.8.8", ""):
            with self.assertRaises(ValueError):
                sec.validate_bind_host(bad)

    def test_security_headers_and_size_and_rate_limit_middlewares(self) -> None:
        sec = _import_security()
        clock = FakeClock()

        async def echo_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            total = 0
            while True:
                msg = await receive()
                if msg["type"] == "http.request":
                    total += len(msg.get("body", b""))
                    if not msg.get("more_body", False):
                        break
            payload = json.dumps({"path": scope["path"], "bytes": total}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": payload})

        app = sec.RequestSizeLimitMiddleware(
            echo_app,
            default_max_bytes=1024,
            upload_max_bytes=4096,
            upload_paths=("/people",),
        )
        app = sec.ConsistentRateLimitMiddleware(
            app,
            read_max_requests=3,
            write_max_requests=2,
            auth_max_requests=2,
            window_seconds=60,
            monotonic_fn=clock,
        )
        app = sec.SecurityHeadersMiddleware(app)

        status_code, headers, _ = _invoke_asgi(
            app,
            method="GET",
            path="/health",
            headers={"X-Forwarded-Proto": "https"},
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertEqual(headers["cross-origin-resource-policy"], "same-origin")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("max-age=31536000", headers["strict-transport-security"])
        self.assertEqual(headers["x-ratelimit-limit"], "3")
        self.assertEqual(headers["x-ratelimit-remaining"], "2")

        # Exceed read tier limit (3 allowed, 4th -> 429)
        self.assertEqual(_invoke_asgi(app, method="GET", path="/health")[0], 200)
        self.assertEqual(_invoke_asgi(app, method="GET", path="/health")[0], 200)
        s4, h4, _ = _invoke_asgi(app, method="GET", path="/health")
        self.assertEqual(s4, 429)
        self.assertIn("retry-after", h4)
        self.assertEqual(h4["x-frame-options"], "DENY")

        # Auth tier is independent and capped at 2
        self.assertEqual(_invoke_asgi(app, method="POST", path="/auth/refresh", body_chunks=[b"{}"])[0], 200)
        self.assertEqual(_invoke_asgi(app, method="POST", path="/auth/refresh", body_chunks=[b"{}"])[0], 200)
        self.assertEqual(_invoke_asgi(app, method="POST", path="/auth/refresh", body_chunks=[b"{}"])[0], 429)

        # Advance past window -> quota resets; test both Content-Length and chunked 413
        clock.now += 61.0
        s_cl, h_cl, _ = _invoke_asgi(
            app,
            method="POST",
            path="/auth/refresh",
            headers={"Content-Length": "2048"},
            body_chunks=[b"x" * 2048],
        )
        self.assertEqual(s_cl, 413)
        self.assertEqual(h_cl["x-content-type-options"], "nosniff")

        s_chunked, _, _ = _invoke_asgi(
            app,
            method="POST",
            path="/zones",
            body_chunks=[b"a" * 700, b"b" * 700],
        )
        self.assertEqual(s_chunked, 413)

        # Upload path /people uses higher upload_max_bytes budget (4096)
        self.assertEqual(
            _invoke_asgi(
                app,
                method="POST",
                path="/people",
                headers={"Content-Length": "2048"},
                body_chunks=[b"x" * 2048],
            )[0],
            200,
        )
        # Reset write window before testing oversized upload rejection
        clock.now += 61.0
        self.assertEqual(
            _invoke_asgi(
                app,
                method="POST",
                path="/people",
                body_chunks=[b"x" * 2500, b"y" * 2500],
            )[0],
            413,
        )

    def test_secret_scan_and_gitignore(self) -> None:
        secret_scan = importlib.import_module("tools.secret_scan")
        self.assertEqual(secret_scan.check_gitignore(PROJECT_ROOT), [])
        self.assertEqual(secret_scan.scan_repository(PROJECT_ROOT), [])


if __name__ == "__main__":
    unittest.main()
