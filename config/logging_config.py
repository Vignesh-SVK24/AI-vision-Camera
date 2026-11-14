"""Logging setup with rotating file handler, console handler, and secret masking."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from typing import Iterable, Optional
from urllib.parse import quote

from config.settings import Settings

MASK = "****"

# Matches credentials embedded in URLs like rtsp://user:password@host:554/stream1
_URL_CREDENTIALS_RE = re.compile(
    r"((?:rtsp|rtsps|http|https)://[^:\s/@]+:)([^@\s/]+)(@)",
    re.IGNORECASE,
)

# Matches key=value or key: value for common secret field names
_KV_SECRET_RE = re.compile(
    r"((?:camera_pass|password|passwd|pwd|secret|token|api_key)\s*[=:]\s*)([^\s,;'\"]+)",
    re.IGNORECASE,
)


def mask_sensitive_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Mask URL passwords, secret key/value pairs, and explicit secret strings."""
    if not text:
        return text

    masked = _URL_CREDENTIALS_RE.sub(rf"\1{MASK}\3", text)
    masked = _KV_SECRET_RE.sub(rf"\1{MASK}", masked)

    for secret in secrets:
        if not secret:
            continue
        masked = masked.replace(secret, MASK)
        encoded = quote(secret, safe="")
        if encoded and encoded != secret:
            masked = masked.replace(encoded, MASK)

    return masked


class PasswordMaskingFilter(logging.Filter):
    """Logging filter that scrubs passwords and secrets from log records."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: list[str] = [s for s in secrets if s]

    def add_secret(self, secret: str) -> None:
        """Register an additional secret string to mask at runtime."""
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = mask_sensitive_text(rendered, self._secrets)
        record.args = ()
        if record.exc_text:
            record.exc_text = mask_sensitive_text(record.exc_text, self._secrets)
        return True


class MaskingFormatter(logging.Formatter):
    """Formatter that masks sensitive values across message and exception text."""

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        secrets: Iterable[str] = (),
    ) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._secrets: list[str] = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return mask_sensitive_text(formatted, self._secrets)


def setup_logging(
    settings: Settings,
    extra_secrets: Optional[Iterable[str]] = None,
) -> logging.Logger:
    """Configure root logging with rotating file and console handlers."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    all_secrets = list(settings.secrets())
    if extra_secrets:
        all_secrets.extend(s for s in extra_secrets if s)

    mask_filter = PasswordMaskingFilter(all_secrets)
    formatter = MaskingFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        secrets=all_secrets,
    )

    root_logger = logging.getLogger()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Clear existing handlers cleanly so repeated setup calls do not duplicate output.
    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)
        try:
            existing.close()
        except Exception:
            pass

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(mask_filter)

    file_handler = RotatingFileHandler(
        filename=settings.log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(mask_filter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    return root_logger
