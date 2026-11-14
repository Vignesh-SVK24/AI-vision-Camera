"""Telegram Bot API alerts, configurable rules, rate limiting, and outbox worker."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from config.logging_config import mask_sensitive_text
from config.settings import BASE_DIR, Settings, get_settings
from events.event_manager import (
    EVENT_CAMERA_OFFLINE,
    EVENT_CAMERA_ONLINE,
    EVENT_PERSON_DETECTED,
    EVENT_SYSTEM_ERROR,
    EVENT_ZONE_INTRUSION,
)
from storage.db import Database
from storage.snapshots import resolve_snapshot_path

logger = logging.getLogger(__name__)

# (url, body_bytes, headers, timeout_seconds) -> (status_code, response_bytes)
TelegramTransport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


class TelegramError(RuntimeError):
    """Base error for Telegram notification failures."""


class TelegramConfigError(TelegramError):
    """Raised when Telegram credentials are missing."""


class TelegramDeliveryError(TelegramError):
    """Raised on transient network or API delivery failures."""

    def __init__(self, message: str, *, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def default_telegram_transport(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    """Send an HTTP POST request using Python's standard library urllib."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        raw_body = b""
        try:
            raw_body = exc.read()
        except Exception:
            pass
        return int(exc.code), raw_body
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TelegramDeliveryError(f"Network error reaching Telegram API: {exc}") from exc


def _encode_multipart(
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    content_type: str = "image/jpeg",
) -> tuple[bytes, str]:
    boundary = f"----VisionBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for key, val in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(f"{val}\r\n".encode("utf-8"))

    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode(
            "utf-8"
        )
    )
    chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class TelegramClient:
    """Minimal dependency-free Telegram Bot API client."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout: float = 15.0,
        transport: Optional[TelegramTransport] = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        cfg = settings or get_settings()
        self._bot_token = (cfg.telegram_bot_token if bot_token is None else bot_token).strip()
        self._chat_id = (cfg.telegram_chat_id if chat_id is None else chat_id).strip()
        self._timeout = float(timeout)
        self._transport = transport or default_telegram_transport
        self._api_base_url = api_base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def _scrub(self, text: str) -> str:
        secrets = (self._bot_token,) if self._bot_token else ()
        return mask_sensitive_text(text, secrets)

    def _api_url(self, method: str) -> str:
        if not self.is_configured:
            raise TelegramConfigError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env."
            )
        return f"{self._api_base_url}/bot{self._bot_token}/{method}"

    def _post(self, method: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        url = self._api_url(method)
        try:
            status, resp_bytes = self._transport(url, body, headers, self._timeout)
        except TelegramDeliveryError as exc:
            raise TelegramDeliveryError(self._scrub(str(exc)), retry_after=exc.retry_after) from exc
        except Exception as exc:
            raise TelegramDeliveryError(self._scrub(str(exc))) from exc

        try:
            payload = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
        except Exception:
            payload = {}

        if status == 429:
            retry_after = None
            params = payload.get("parameters") if isinstance(payload, dict) else None
            if isinstance(params, dict) and "retry_after" in params:
                try:
                    retry_after = float(params["retry_after"])
                except (TypeError, ValueError):
                    retry_after = None
            raise TelegramDeliveryError(
                f"Telegram rate limit (HTTP 429): {self._scrub(str(payload))}",
                retry_after=retry_after,
            )

        if status >= 400 or not payload.get("ok", False):
            desc = (
                payload.get("description")
                if isinstance(payload, dict) and payload.get("description")
                else f"HTTP {status}"
            )
            raise TelegramDeliveryError(
                f"Telegram API error ({method}): {self._scrub(str(desc))}"
            )

        return payload

    def send_message(self, text: str, *, disable_notification: bool = False) -> dict[str, Any]:
        """Send a text message via Telegram sendMessage."""
        payload = json.dumps(
            {
                "chat_id": self._chat_id,
                "text": text[:4096],
                "disable_notification": bool(disable_notification),
            }
        ).encode("utf-8")
        return self._post(
            "sendMessage",
            payload,
            {"Content-Type": "application/json"},
        )

    def send_photo(
        self,
        photo_path: Path | str,
        *,
        caption: str = "",
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """Send a JPEG snapshot with caption via Telegram sendPhoto."""
        path = Path(photo_path)
        file_bytes = path.read_bytes()
        fields = {
            "chat_id": self._chat_id,
            "caption": caption[:1024],
            "disable_notification": "true" if disable_notification else "false",
        }
        body, content_type = _encode_multipart(
            fields,
            file_field="photo",
            filename=path.name,
            file_bytes=file_bytes,
            content_type="image/jpeg",
        )
        return self._post("sendPhoto", body, {"Content-Type": content_type})

    def send_alert(
        self,
        text: str,
        *,
        snapshot_path: Optional[str | Path] = None,
        disable_notification: bool = False,
        base_dir: Path = BASE_DIR,
    ) -> dict[str, Any]:
        """Send an alert with snapshot if available on disk, else text-only."""
        if snapshot_path:
            resolved = (
                snapshot_path
                if isinstance(snapshot_path, Path)
                else resolve_snapshot_path(str(snapshot_path), base_dir=base_dir)
            )
            if resolved.exists() and resolved.is_file():
                return self.send_photo(
                    resolved,
                    caption=text,
                    disable_notification=disable_notification,
                )

        return self.send_message(text, disable_notification=disable_notification)


@dataclass(frozen=True)
class AlertDecision:
    """Routing decision for an event based on configured alert rules."""

    send: bool
    high_priority: bool
    reason: str


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_person_identity(event_row: dict[str, Any]) -> tuple[str, bool]:
    """Extract (person_name_or_Unknown, is_known_family) from an event/outbox row."""
    meta = _parse_metadata(event_row.get("metadata_json"))

    db_name = event_row.get("person_name")
    meta_name = meta.get("person_name")
    raw_name = db_name if db_name else meta_name

    if raw_name and str(raw_name).strip() and str(raw_name).strip().lower() != "unknown":
        is_trusted_db = event_row.get("person_is_trusted")
        if is_trusted_db is not None:
            is_known = bool(int(is_trusted_db))
        elif "is_trusted" in meta:
            is_known = bool(meta["is_trusted"])
        else:
            is_known = True
        return str(raw_name).strip(), is_known

    return "Unknown", False


def evaluate_alert_rule(event_row: dict[str, Any], settings: Settings) -> AlertDecision:
    """Apply configurable alert priority and suppression rules to an event row."""
    event_type = str(event_row.get("event_type", ""))

    if event_type == EVENT_ZONE_INTRUSION:
        return AlertDecision(
            send=settings.alert_on_zone_intrusion,
            high_priority=True,
            reason="zone_intrusion_high_priority",
        )

    if event_type == EVENT_PERSON_DETECTED:
        person_name, is_known = resolve_person_identity(event_row)
        if is_known:
            return AlertDecision(
                send=settings.alert_on_known_person,
                high_priority=False,
                reason=f"known_family_{person_name}",
            )
        return AlertDecision(
            send=settings.alert_on_unknown_person,
            high_priority=True,
            reason="unknown_person_high_priority",
        )

    if event_type == EVENT_CAMERA_OFFLINE:
        return AlertDecision(
            send=settings.alert_on_camera_offline,
            high_priority=True,
            reason="camera_offline",
        )

    if event_type == EVENT_CAMERA_ONLINE:
        return AlertDecision(
            send=settings.alert_on_camera_online,
            high_priority=False,
            reason="camera_online",
        )

    if event_type == EVENT_SYSTEM_ERROR:
        return AlertDecision(
            send=settings.alert_on_system_error,
            high_priority=True,
            reason="system_error",
        )

    return AlertDecision(send=False, high_priority=False, reason="unhandled_event_type")


def format_alert_message(
    event_row: dict[str, Any],
    *,
    camera_name: str = "Front Camera",
    high_priority: bool = True,
) -> str:
    """Format a Telegram alert with type, camera name, person name or Unknown, and time."""
    event_type = str(event_row.get("event_type", "unknown"))
    meta = _parse_metadata(event_row.get("metadata_json"))
    cam = str(meta.get("camera_name") or camera_name)
    created_at = str(event_row.get("event_created_at") or event_row.get("created_at") or "")

    if event_type in (EVENT_PERSON_DETECTED, EVENT_ZONE_INTRUSION):
        person_label, _ = resolve_person_identity(event_row)
    else:
        person_label = "N/A"

    tag = "[HIGH PRIORITY]" if high_priority else "[INFO]"
    lines = [
        f"{tag} {event_type}",
        f"Type: {event_type}",
        f"Camera: {cam}",
        f"Person: {person_label}",
        f"Time: {created_at}",
    ]

    zone_name = event_row.get("zone_name")
    if zone_name:
        lines.append(f"Zone: {zone_name}")

    message = event_row.get("message")
    if message:
        lines.append(f"Details: {message}")

    return "\n".join(lines)


class RateLimiter:
    """Thread-safe sliding-window and minimum-interval rate limiter."""

    def __init__(
        self,
        *,
        max_per_minute: int = 20,
        min_interval_seconds: float = 0.5,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._max_per_minute = max(1, int(max_per_minute))
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._monotonic = monotonic_fn
        self._sleep = sleep_fn
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, stop_event: Optional[threading.Event] = None) -> bool:
        """Wait until a send slot is available; return False if interrupted by stop_event."""
        while True:
            if stop_event is not None and stop_event.is_set():
                return False

            wait_for = 0.0
            with self._lock:
                now = self._monotonic()
                while self._timestamps and (now - self._timestamps[0]) >= 60.0:
                    self._timestamps.popleft()

                if self._timestamps and self._min_interval > 0.0:
                    since_last = now - self._timestamps[-1]
                    if since_last < self._min_interval:
                        wait_for = max(wait_for, self._min_interval - since_last)

                if len(self._timestamps) >= self._max_per_minute:
                    window_wait = 60.0 - (now - self._timestamps[0])
                    wait_for = max(wait_for, window_wait)

                if wait_for <= 0.0:
                    self._timestamps.append(now)
                    return True

            if stop_event is not None:
                if stop_event.wait(wait_for):
                    return False
            else:
                self._sleep(wait_for)


@dataclass(frozen=True)
class OutboxBatchResult:
    """Result of one outbox processing pass."""

    sent: int
    quietly_logged: int
    failed: int
    retry_after: Optional[float] = None


class OutboxWorker:
    """Background worker delivering queued outbox events to Telegram with backoff."""

    def __init__(
        self,
        db: Database,
        client: Optional[TelegramClient] = None,
        settings: Optional[Settings] = None,
        *,
        rate_limiter: Optional[RateLimiter] = None,
        poll_interval: Optional[float] = None,
        backoff_initial: Optional[float] = None,
        backoff_max: Optional[float] = None,
        base_dir: Path = BASE_DIR,
    ) -> None:
        cfg = settings or get_settings()
        self._db = db
        self._settings = cfg
        self._client = client or TelegramClient(settings=cfg)
        self._rate_limiter = rate_limiter or RateLimiter(
            max_per_minute=cfg.telegram_rate_limit_per_min,
            min_interval_seconds=cfg.telegram_min_interval_seconds,
        )
        self._poll_interval = (
            cfg.telegram_poll_interval if poll_interval is None else float(poll_interval)
        )
        self._backoff_initial = (
            cfg.telegram_backoff_initial
            if backoff_initial is None
            else float(backoff_initial)
        )
        self._backoff_max = (
            cfg.telegram_backoff_max if backoff_max is None else float(backoff_max)
        )
        self._base_dir = base_dir

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._attempt = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wake(self) -> None:
        """Wake the worker immediately after inserting a new urgent event."""
        self._wake_event.set()

    def process_once(self, *, batch_size: int = 50) -> OutboxBatchResult:
        """Process currently pending outbox rows in FIFO order."""
        pending = self._db.get_pending_outbox(
            limit=batch_size,
            pending_notify=True,
        )
        if not pending:
            return OutboxBatchResult(sent=0, quietly_logged=0, failed=0)

        sent = 0
        quietly_logged = 0

        for item in pending:
            if self._stop_event.is_set():
                break

            outbox_id = int(item["id"])
            event_id = int(item["event_id"])
            decision = evaluate_alert_rule(item, self._settings)

            if not decision.send:
                logger.info(
                    "Quietly logged event id=%d (%s, rule=%s).",
                    event_id,
                    item.get("event_type"),
                    decision.reason,
                )
                self._db.mark_outbox_notified(outbox_id)
                quietly_logged += 1
                continue

            if not self._settings.telegram_enabled or not self._client.is_configured:
                logger.debug(
                    "Telegram notifications disabled or unconfigured; event id=%d remains queued.",
                    event_id,
                )
                return OutboxBatchResult(sent=sent, quietly_logged=quietly_logged, failed=0)

            if not self._rate_limiter.acquire(self._stop_event):
                break

            text = format_alert_message(
                item,
                camera_name=self._settings.camera_name,
                high_priority=decision.high_priority,
            )

            try:
                self._client.send_alert(
                    text,
                    snapshot_path=item.get("snapshot_path"),
                    disable_notification=not decision.high_priority,
                    base_dir=self._base_dir,
                )
            except TelegramDeliveryError as exc:
                masked_err = mask_sensitive_text(str(exc), self._settings.secrets())
                self._db.record_outbox_notify_error(outbox_id, masked_err)
                logger.warning(
                    "Telegram delivery failed for event id=%d (queued for retry): %s",
                    event_id,
                    masked_err,
                )
                return OutboxBatchResult(
                    sent=sent,
                    quietly_logged=quietly_logged,
                    failed=1,
                    retry_after=exc.retry_after,
                )

            self._db.mark_outbox_notified(outbox_id)
            sent += 1
            self._attempt = 0

        return OutboxBatchResult(sent=sent, quietly_logged=quietly_logged, failed=0)

    def start(self) -> "OutboxWorker":
        if self.is_running:
            return self
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="telegram-outbox-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info("Telegram outbox worker started.")
        return self

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("Telegram outbox worker stopped.")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            result = self.process_once()

            if result.failed > 0:
                self._attempt += 1
                backoff = min(
                    self._backoff_initial * (2 ** (self._attempt - 1)),
                    self._backoff_max,
                )
                wait_for = max(backoff, result.retry_after or 0.0)
                logger.info(
                    "Outbox worker backing off for %.2fs before retrying queued alerts.",
                    wait_for,
                )
                self._stop_event.wait(wait_for)
            else:
                self._attempt = 0
                # Wait for poll interval or immediate wake signal (unless stopped).
                self._wake_event.wait(self._poll_interval)
