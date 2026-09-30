"""
Authentication routes.

Endpoints:
    GET  /login         -- render login form
    POST /login         -- accept form, set session cookie, redirect to dashboard
    POST /api/login     -- JSON variant for API clients (returns token)
    POST /logout        -- clear cookie, redirect to landing
    GET  /me            -- current user info (JSON, requires auth)

Notes on the cookie:
    - HttpOnly  -- JS can't read it; mitigates XSS token theft
    - Secure    -- only sent over HTTPS in production
    - SameSite  -- 'lax' so links from email still work
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.jwt import create_access_token
from app.auth.password import needs_rehash, hash_password, verify_password
from app.auth.session import (
    clear_session_cookie,
    issue_token_for,
    set_session_cookie,
)
from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from app.services import audit_service
from app.templating import templates

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _attempt_login(db: Session, email: str, password: str) -> User:
    """
    Verify credentials. Returns the user on success or raises HTTPException.
    Uses the same error message on missing-user and bad-password to avoid
    leaking which emails are registered.
    """
    user = db.query(User).filter(User.Email == email).one_or_none()
    if user is None:
        # Run a real bcrypt verify so timing is comparable to the success path.
        # The hash below is a valid bcrypt of an internal sentinel string that
        # no real user can submit. Doing the work prevents timing oracles that
        # distinguish "no such user" from "wrong password".
        verify_password(
            password,
            "$2b$12$ogPY6123248LojPUHVMSIOKw4Lp2MRKXAem./k.7eni8aZLgONZJC",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )
    if not user.IsActive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )
    # Reject login if the user's Subscriber is no longer active.
    if user.SubscriberID is not None:
        from sqlalchemy.orm import joinedload
        user = (
            db.query(User)
            .options(joinedload(User.subscriber))
            .filter(User.UserID == user.UserID)
            .one()
        )
        if user.subscriber and user.subscriber.SubscriberStatus not in ("active", "expired_grace"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account subscription is no longer active.",
            )
    if not verify_password(password, user.PasswordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Opportunistic rehash if bcrypt cost was increased since this user
    # last set their password.
    if needs_rehash(user.PasswordHash):
        user.PasswordHash = hash_password(password)
    user.LastLogin = datetime.now(tz=timezone.utc)
    db.commit()
    return user


# Cookie + token helpers now live in app.auth.session (shared with the
# accept-invitation flow). Aliased here to keep existing call sites unchanged.
_set_session_cookie = set_session_cookie
_clear_session_cookie = clear_session_cookie
_issue_token_for = issue_token_for


# ---------------------------------------------------------------------------
# HTML login (browser flow)
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Render the login form."""
    reason = request.query_params.get("reason")
    error = None
    if reason == "session_ended":
        error = "Your session was ended because you logged in from another location. Please log in again."
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"error": error, "email": ""},
    )


@router.post("/login")
def login_submit(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_db)],
):
    """Handle login form submission. Sets session cookie and redirects."""
    from app.middleware.rate_limit import check_rate_limit, record_failed_attempt, reset_attempts

    # Rate limit check before any DB work
    allowed, retry_after = check_rate_limit(request)
    if not allowed:
        minutes = retry_after // 60
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "error": f"Too many failed attempts. Please try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                "email": email,
            },
            status_code=429,
        )

    try:
        user = _attempt_login(db, email.strip().lower(), password)
    except HTTPException as e:
        record_failed_attempt(request)
        audit_service.record(
            db,
            action="login_failed",
            details=f"email={email}",
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        # Re-render form with error
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={"error": e.detail, "email": email},
            status_code=e.status_code,
        )

    reset_attempts(request)

    token, _ = _issue_token_for(user, db)
    db.commit()  # persist SessionVersion bump
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token)
    audit_service.record(
        db,
        action="login",
        actor_user_id=user.UserID,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    """Render the forgot-password request form."""
    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={"sent": False, "error": None},
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    email: Annotated[str, Form()] = "",
):
    """
    Always shows the same success message regardless of whether the email
    matched an account -- never reveal which emails are registered.
    """
    from app.services import password_reset_service
    from app.services.email_service import send_password_reset_email

    token_row = password_reset_service.request_reset(db, email)
    if token_row is not None:
        db.commit()
        send_password_reset_email(to_email=email.strip().lower(), token=token_row.Token)
    else:
        db.rollback()

    return templates.TemplateResponse(
        request=request,
        name="auth/forgot_password.html",
        context={"sent": True, "error": None},
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: str = "",
):
    from app.services import password_reset_service

    valid = password_reset_service.validate_token(db, token) is not None
    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        context={"token": token, "valid": valid, "error": None},
    )


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
):
    from app.auth.password import hash_password, validate_password, PasswordPolicyError
    from app.services import password_reset_service
    from app.services.password_reset_service import PasswordResetError

    def _rerender(error: str, valid: bool = True):
        return templates.TemplateResponse(
            request=request,
            name="auth/reset_password.html",
            context={"token": token, "valid": valid, "error": error},
            status_code=400,
        )

    if password != confirm_password:
        return _rerender("Passwords do not match.")

    try:
        validate_password(password)
    except PasswordPolicyError as e:
        return _rerender(str(e))

    try:
        password_reset_service.redeem_token(db, token, hash_password(password))
        db.commit()
    except PasswordResetError as e:
        db.rollback()
        return _rerender(str(e), valid=False)

    return templates.TemplateResponse(
        request=request,
        name="auth/reset_password.html",
        context={"token": token, "valid": False, "error": None, "success": True},
    )


@router.post("/logout")
def logout(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    """Clear session cookie and redirect to landing. Works even if not logged in."""
    from app.deps import get_current_user_optional
    # Manual resolution (can't use Depends here since we want a fresh response object)
    cookie = request.cookies.get(settings.session_cookie_name)
    user = None
    if cookie:
        try:
            from app.auth.jwt import decode_token
            payload = decode_token(cookie)
            user = db.get(User, int(payload["sub"]))
        except Exception:
            user = None

    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookie(response)
    if user:
        audit_service.record(
            db,
            action="logout",
            actor_user_id=user.UserID,
            ip_address=_client_ip(request),
        )
    return response


# ---------------------------------------------------------------------------
# JSON API flavour
# ---------------------------------------------------------------------------
@router.post("/api/login", response_model=TokenResponse)
def api_login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """JSON login: returns a token AND sets the cookie (so the same token works
    in browser-based clients and curl-style scripts)."""
    user = _attempt_login(db, payload.email.lower(), payload.password)
    token, expires_at = _issue_token_for(user, db)
    db.commit()
    _set_session_cookie(response, token)
    audit_service.record(
        db,
        action="login",
        actor_user_id=user.UserID,
        details="api",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/api/me", response_model=CurrentUserResponse)
def me(user: Annotated[User, Depends(get_current_user)]):
    """Return the current user. Useful for API clients verifying their token."""
    return CurrentUserResponse(
        user_id=user.UserID,
        email=user.Email,
        full_name=user.FullName,
        user_type=user.UserType,
        subscriber_id=user.SubscriberID,
        last_login=user.LastLogin,
    )


# ---------------------------------------------------------------------------
# small helper
# ---------------------------------------------------------------------------
def _client_ip(request: Request) -> str | None:
    """
    Return the best guess at the client IP. If we're behind a reverse proxy
    (IIS / Caddy), trust X-Forwarded-For's leftmost value.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


@router.post("/set-currency")
def set_currency(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    currency: Annotated[str, Form()] = "ZAR",
):
    """Stores ZAR/USD display preference in a lightweight cookie."""
    from fastapi.responses import Response
    if currency not in ("ZAR", "USD"):
        currency = "ZAR"
    response = Response(status_code=204)
    response.set_cookie(
        key="currency",
        value=currency,
        max_age=60 * 60 * 24 * 365,  # 1 year
        httponly=False,  # JS needs to read this for the toggle UI
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return response
