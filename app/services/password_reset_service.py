"""
app/services/password_reset_service.py

Self-service "Forgot password" flow.

    1. request_reset(email)        -- creates a token if the email matches
                                       an active user, sends the reset email.
                                       Always returns the same response to
                                       the caller regardless of whether the
                                       email matched, so the login page
                                       never leaks which emails have accounts.
    2. validate_token(token)       -- returns the token row if valid (not
                                       used, not expired), else None.
    3. redeem_token(token, new_pw) -- sets the new password, marks the token
                                       used, bumps SessionVersion (logs out
                                       any existing sessions for safety).

Mirrors invitation_service.py's token pattern closely.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.password_reset import PasswordResetToken
from app.models.user import User

RESET_TTL_HOURS = 2
_TOKEN_BYTES = 32


class PasswordResetError(Exception):
    pass


def _utc_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def request_reset(db: Session, email: str) -> Optional[PasswordResetToken]:
    """
    Creates a reset token if email matches an active user. Returns None
    (silently) if no match -- callers must show the same success message
    either way, so the login page never reveals which emails are registered.
    """
    email = (email or "").strip().lower()
    if not email:
        return None

    user = (
        db.query(User)
        .filter(User.Email == email, User.IsActive == True)  # noqa: E712
        .one_or_none()
    )
    if user is None:
        return None

    # Invalidate any prior unused tokens for this user before issuing a new
    # one -- only the most recent reset link should ever work.
    db.query(PasswordResetToken).filter(
        PasswordResetToken.UserID == user.UserID,
        PasswordResetToken.UsedAt.is_(None),
    ).update({"UsedAt": _utc_naive()})

    token = PasswordResetToken(
        UserID=user.UserID,
        Token=generate_token(),
        ExpiresAt=_utc_naive() + timedelta(hours=RESET_TTL_HOURS),
        CreatedAt=_utc_naive(),
    )
    db.add(token)
    db.flush()
    return token


def validate_token(db: Session, token: str) -> Optional[PasswordResetToken]:
    """Returns the token row if it exists and is currently valid, else None."""
    if not token:
        return None
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.Token == token)
        .one_or_none()
    )
    if row is None or not row.is_valid:
        return None
    return row


def redeem_token(db: Session, token: str, password_hash: str) -> User:
    """
    Sets the new password hash, marks the token used, and bumps
    SessionVersion so any existing logged-in sessions for this user are
    invalidated (the same mechanism used for normal login).
    """
    row = validate_token(db, token)
    if row is None:
        raise PasswordResetError("This reset link is invalid or has expired.")

    user = db.get(User, row.UserID)
    if user is None or not user.IsActive:
        raise PasswordResetError("This account is no longer active.")

    user.PasswordHash = password_hash
    user.SessionVersion = (user.SessionVersion or 1) + 1
    row.UsedAt = _utc_naive()
    db.flush()
    return user
