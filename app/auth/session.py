"""
Shared session-cookie helpers.

Both the login flow and the accept-invitation flow need to issue a JWT and
drop it into the session cookie. This module is the single place that knows
how to do that, so the two flows stay consistent.
"""

from datetime import datetime, timedelta, timezone

from fastapi import Response

from app.auth.jwt import create_access_token
from app.config import settings
from app.models.user import User


def issue_token_for(user: User, db=None) -> tuple[str, datetime]:
    """
    Create an access token for a user and return (token, expiry_utc).
    If db is supplied, bumps SessionVersion to invalidate prior sessions.
    """
    if db is not None:
        user.SessionVersion = (user.SessionVersion or 1) + 1
        db.flush()
    token = create_access_token(
        user_id=user.UserID,
        email=user.Email,
        user_type=user.UserType,
        subscriber_id=user.SubscriberID,
        session_version=user.SessionVersion or 1,
    )
    expires_at = (
        datetime.now(tz=timezone.utc).replace(microsecond=0)
        + timedelta(minutes=settings.jwt_access_token_minutes)
    )
    return token, expires_at


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.jwt_access_token_minutes * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.session_cookie_name, path="/")
