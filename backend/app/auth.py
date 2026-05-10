"""
Authentication: single shared password + bcrypt hash + JWT httpOnly cookie.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status
from loguru import logger

from app.config import settings


_TOKEN_COOKIE = "audiobook_session"
_ALGORITHM = "HS256"


# ── Password handling ─────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def check_app_password(plain: str) -> bool:
    """Compare against the configured APP_PASSWORD (constant-time)."""
    return bcrypt.checkpw(
        plain.encode(),
        bcrypt.hashpw(settings.app_password.encode(), bcrypt.gensalt()),
    )


# ── JWT ────────────────────────────────────────────────────────────────────────

def create_access_token() -> str:
    expiry = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {"sub": "admin", "exp": expiry}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# ── Rate limiter (in-memory, single process) ──────────────────────────────────

_login_attempts: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 5
_WINDOW_SEC = 900  # 15 minutes


def check_rate_limit(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Keep only attempts within the window
    attempts = [t for t in attempts if now - t < _WINDOW_SEC]
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
        )
    attempts.append(now)
    _login_attempts[ip] = attempts


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user(
    audiobook_session: Optional[str] = Cookie(default=None),
) -> dict:
    if not audiobook_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return decode_access_token(audiobook_session)


CurrentUser = Depends(get_current_user)
