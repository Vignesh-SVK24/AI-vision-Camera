"""Argon2 password hashing, JWT access/refresh tokens, and owner account creation."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, status

from config.settings import Settings
from storage.db import Database

MIN_PASSWORD_LENGTH = 8
JWT_ALGORITHM = "HS256"

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    if not password_hash or not password:
        return False
    try:
        return bool(_password_hasher.verify(password_hash, password))
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_owner_account(
    db: Database,
    *,
    username: str,
    password: str,
) -> int:
    """Create the owner account from CLI (no open API registration exists)."""
    clean_user = username.strip()
    if not clean_user:
        raise ValueError("Username must not be empty.")
    if db.get_user_by_username(clean_user) is not None:
        raise ValueError(f"User '{clean_user}' already exists.")

    pw_hash = hash_password(password)
    user_id = db.create_user(
        username=clean_user,
        password_hash=pw_hash,
        role="owner",
    )
    db.record_audit(
        actor="cli",
        action="create_owner",
        target_type="user",
        target_id=str(user_id),
        details={"username": clean_user, "role": "owner"},
    )
    return user_id


class AuthService:
    """Issues and validates JWT access and refresh tokens."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret_key or secrets.token_urlsafe(48)
        self._access_ttl = timedelta(minutes=settings.jwt_access_ttl_minutes)
        self._refresh_ttl = timedelta(days=settings.jwt_refresh_ttl_days)

    def create_access_token(self, user: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user["id"]),
            "username": str(user["username"]),
            "role": str(user["role"]),
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + self._access_ttl).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)

    def create_refresh_token(self, user: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user["id"]),
            "username": str(user["username"]),
            "role": str(user["role"]),
            "type": "refresh",
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + self._refresh_ttl).timestamp()),
        }
        return jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)

    def decode_token(self, token: str, *, expected_type: str) -> dict[str, Any]:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired.",
            ) from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token.",
            ) from exc

        if payload.get("type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token type; expected '{expected_type}'.",
            )
        return payload
