"""
JWT token issuance and verification.

Token payload (claims) v0.3:
    sub          -- str(UserID), the subject
    email        -- user's email (convenience; saves a DB hit for display)
    type         -- UserType value (internal/subscriber_admin/subscriber_user)
    subscriber_id -- int or None (NULL for internal users)
    imp_session  -- ImpersonationSessionID if currently impersonating, else absent
    iat, exp     -- issued-at, expiration (standard)
    token_use    -- 'access' or 'refresh'
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError

from app.config import settings


class TokenError(Exception):
    """Raised when a token is invalid, expired, or fails verification."""


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(
    user_id: int,
    email: str,
    user_type: str,
    subscriber_id: int | None = None,
    session_version: int = 1,
    impersonation_session_id: int | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Issue a signed access token for the given user."""
    minutes = expires_minutes or settings.jwt_access_token_minutes
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "type": user_type,
        "subscriber_id": subscriber_id,
        "session_version": session_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "token_use": "access",
    }
    if impersonation_session_id is not None:
        payload["imp_session"] = impersonation_session_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises TokenError on any failure."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as e:
        raise TokenError("Token expired") from e
    except InvalidTokenError as e:
        raise TokenError(f"Invalid token: {e}") from e

    if "sub" not in payload or "type" not in payload:
        raise TokenError("Malformed token payload")
    return payload
