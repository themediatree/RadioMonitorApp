"""
FastAPI dependencies for authentication and tenancy.

The two main dependencies:
    - get_current_user            -> raises 401 if not authenticated
    - get_current_user_optional   -> returns None if not authenticated (for
                                     pages that render differently based on
                                     auth state, e.g. landing page navbar)

Plus a few role enforcers as Depends-able callables:
    - require_internal
    - require_admin_within_tenant
"""

from typing import Annotated, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.jwt import TokenError, decode_token
from app.auth.permissions import (
    enforce_active,
    enforce_admin_within_tenant,
    enforce_internal,
)
from app.config import settings
from app.database import get_db
from app.models.user import User


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _extract_token(
    session_cookie: Optional[str],
    authorization: Optional[str],
) -> Optional[str]:
    """
    Prefer cookie (for browser sessions). Fall back to Authorization header
    (for API clients sending 'Bearer <token>').
    """
    if session_cookie:
        return session_cookie
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(None, 1)[1].strip()
    return None


def _load_user(db: Session, user_id_str: str) -> User:
    try:
        user_id = int(user_id_str)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
        ) from e

    from sqlalchemy.orm import joinedload
    user = (
        db.query(User)
        .options(joinedload(User.subscriber))
        .filter(User.UserID == user_id)
        .one_or_none()
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )
    return user


# ---------------------------------------------------------------------------
# Public dependencies
# ---------------------------------------------------------------------------
def _resolve_user(
    session_cookie: Optional[str],
    authorization: Optional[str],
    db: Session,
) -> Optional[User]:
    token = _extract_token(session_cookie, authorization)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except TokenError:
        return None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.IsActive:
        return None
    return user


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    session_cookie: Annotated[
        Optional[str], Cookie(alias=settings.session_cookie_name)
    ] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> User:
    """
    Resolve the authenticated user. Raises 401 if anything is missing or invalid.
    Use as: user: User = Depends(get_current_user)
    """
    token = _extract_token(session_cookie, authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(token)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user = _load_user(db, payload["sub"])
    enforce_active(user)

    # Also enforce Subscriber status — a cancelled/archived Subscriber's users
    # cannot access the system even if their User.IsActive is still True.
    if user.SubscriberID is not None and user.subscriber is not None:
        if user.subscriber.SubscriberStatus not in ("active", "expired_grace"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account subscription is no longer active.",
            )

    # Single-session: token MUST carry session_version and it must match the DB.
    # No default — absence of the claim means an old/invalid token.
    token_version = payload.get("session_version")
    if token_version is None or token_version != (user.SessionVersion or 1):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session_ended",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_optional(
    db: Annotated[Session, Depends(get_db)],
    session_cookie: Annotated[
        Optional[str], Cookie(alias=settings.session_cookie_name)
    ] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Optional[User]:
    """Same as get_current_user but returns None instead of raising."""
    return _resolve_user(session_cookie, authorization, db)


# ---------------------------------------------------------------------------
# Role enforcers (use in dependencies=[...] on a router)
# ---------------------------------------------------------------------------
def require_internal(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    enforce_internal(user)
    return user


def require_admin_within_tenant(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    enforce_admin_within_tenant(user)
    return user


def right_panel_context(user: "User", db, request: Request) -> dict:
    """
    Returns context variables needed by the right panel in base_app.html.
    Call from every route that uses base_app.html.
    """
    from app.services.token_service import get_balance
    from app.services.detection_service import list_commercial_detections
    from app.services.billing_service import get_rate, get_exchange_rate, tokens_to_zar, tokens_to_usd, format_zar, format_usd
    from app.models.subscriber import Subscriber
    from decimal import Decimal

    token_balance = (
        get_balance(db, user.SubscriberID)
        if user.SubscriberID else Decimal("0")
    )

    sub = db.get(Subscriber, user.SubscriberID) if user.SubscriberID else None
    plan_code = sub.SubscriptionPlan if sub else "standard"
    rate = get_rate(db, plan_code)
    fx = get_exchange_rate(db)
    currency = get_currency(request)

    try:
        recent_rows, _ = list_commercial_detections(db, user, page_size=5)
    except Exception:
        recent_rows = []

    from app.models.trial_config import SubscriberTrialConfig
    trial_cfg = (
        db.get(SubscriberTrialConfig, user.SubscriberID)
        if user.SubscriberID and user.user_type.value != "internal"
        else None
    )

    is_postpaid_sub = (sub.BillingMode == "postpaid") if sub else False

    return {
        "token_balance": token_balance,
        "rp_balance_zar": format_zar(tokens_to_zar(token_balance, rate)),
        "rp_balance_usd": format_usd(tokens_to_usd(token_balance, rate, fx)),
        "rp_zar_per_token": float(rate),
        "rp_fx_rate": float(fx),
        "rp_currency": currency,
        "recent_detections": recent_rows,
        "trial_cfg": trial_cfg,
        "is_postpaid": is_postpaid_sub,
    }


def get_currency(request: Request) -> str:
    """Returns the user's currency display preference (ZAR or USD), defaulting to ZAR."""
    return request.cookies.get("currency", "ZAR") if request.cookies.get("currency") in ("ZAR", "USD") else "ZAR"


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

def get_api_key_subscriber(request: Request):
    """
    FastAPI dependency for API key authentication.
    Reads key from Authorization: Bearer <key> header.
    Returns the Subscriber row if valid, raises 401/429 otherwise.
    """
    from fastapi import HTTPException
    from app.services.api_key_service import verify_api_key
    from app.models.subscriber import Subscriber

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API key required. Use: Authorization: Bearer <key>")

    raw_key = auth[7:].strip()

    # Need db -- pull from app state
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        key_row = verify_api_key(db, raw_key)
        if key_row is None:
            # Distinguish rate limit vs bad key by checking prefix
            if raw_key.startswith("noctiv_"):
                raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 100 requests/minute.")
            raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
        sub = db.get(Subscriber, key_row.SubscriberID)
        if sub is None or sub.SubscriberStatus != "active":
            raise HTTPException(status_code=403, detail="Subscriber account is not active.")
        db.commit()
        return sub
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
