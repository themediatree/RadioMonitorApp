"""
Subscriber management routes (v0.3, internal-only).

Replaces the old three-CRUD set (clients / agencies / stations) with one
flat Subscriber CRUD. Type is a field on the row, not a separate URL space.

    GET  /admin/subscribers              list
    GET  /admin/subscribers/new          create form
    POST /admin/subscribers              create
    GET  /admin/subscribers/{id}/edit    edit form
    POST /admin/subscribers/{id}         update

Templates for these screens are minimal in R2 -- the polished UX is R3.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import right_panel_context, require_internal
from app.models.subscriber import (
    ADMIN_SETTABLE_STATUSES,
    SUBSCRIBER_STATUSES,
    SUBSCRIBER_TYPES,
    Subscriber,
)
from app.models.user import User
from app.services import audit_service, subscriber_service
from app.services.subscriber_service import SubscriberError
from app.templating import templates

router = APIRouter(tags=["admin-subscribers"], dependencies=[Depends(require_internal)])


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _parse_optional_int(raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@router.get("/admin/subscribers", response_class=HTMLResponse)
def list_subscribers(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    rows = db.query(Subscriber).order_by(Subscriber.Name).all()
    return templates.TemplateResponse(
        request=request,
        name="admin/subscribers.html",
        context={"user": user, "subscribers": rows},
    )


@router.get("/admin/subscribers/new", response_class=HTMLResponse)
def new_subscriber_form(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="admin/subscriber_form.html",
        context={
            "user": user,
            "subscriber": None,
            "types": SUBSCRIBER_TYPES,
            "statuses": ADMIN_SETTABLE_STATUSES,
            "plans": subscriber_service.active_plans(db),
            "available_stations": subscriber_service.stations_without_subscriber(db),
            "error": None,
            "form": {},
        },
    )


@router.post("/admin/subscribers")
def create_subscriber(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    name: Annotated[str, Form()],
    subscriber_type: Annotated[str, Form()],
    plan: Annotated[str, Form()],
    status_value: Annotated[str, Form(alias="status")] = "active",
    other_description: Annotated[str, Form()] = "",
    station_id: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    company_name: Annotated[str, Form()] = "",
):
    sid = _parse_optional_int(station_id)

    def _rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/subscriber_form.html",
            context={
                "user": user, "subscriber": None,
                "types": SUBSCRIBER_TYPES,
                "statuses": ADMIN_SETTABLE_STATUSES,
                "plans": subscriber_service.active_plans(db),
                "available_stations": subscriber_service.stations_without_subscriber(db),
                "error": error,
                "form": {
                    "name": name, "subscriber_type": subscriber_type, "plan": plan,
                    "status": status_value, "other_description": other_description,
                    "station_id": sid, "company_name": company_name,
                    "contact_email": contact_email, "contact_phone": contact_phone,
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        subscriber = subscriber_service.create_subscriber(
            db,
            name=name,
            subscriber_type=subscriber_type,
            plan=plan,
            status=status_value,
            company_name=company_name,
            other_description=other_description,
            station_id=sid,
            contact_email=contact_email,
            contact_phone=contact_phone,
        )
    except SubscriberError as e:
        return _rerender(str(e))

    db.commit()
    audit_service.record(
        db,
        action="subscriber_created",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber.SubscriberID,
        details=f"name={subscriber.Name} type={subscriber.SubscriberType}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/subscribers/{subscriber_id}/edit", response_class=HTMLResponse)
def edit_subscriber_form(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
):
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        return RedirectResponse(
            "/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/subscriber_form.html",
        context={
            "user": user,
            "subscriber": subscriber,
            "types": SUBSCRIBER_TYPES,
            "statuses": ADMIN_SETTABLE_STATUSES,
            "plans": subscriber_service.active_plans(db),
            "available_stations": [],  # not editable post-create
            "error": None,
            "form": {},
        },
    )


@router.post("/admin/subscribers/{subscriber_id}")
def update_subscriber(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
    name: Annotated[str, Form()],
    plan: Annotated[str, Form()],
    status_value: Annotated[str, Form(alias="status")],
    other_description: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    company_name: Annotated[str, Form()] = "",
):
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        return RedirectResponse(
            "/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER
        )

    def _rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/subscriber_form.html",
            context={
                "user": user, "subscriber": subscriber,
                "types": SUBSCRIBER_TYPES,
                "statuses": ADMIN_SETTABLE_STATUSES,
                "plans": subscriber_service.active_plans(db),
                "available_stations": [],
                "error": error,
                "form": {
                    "name": name, "plan": plan, "status": status_value,
                    "other_description": other_description,
                    "contact_email": contact_email, "contact_phone": contact_phone,
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        subscriber_service.update_subscriber(
            db, subscriber,
            name=name, plan=plan, status=status_value,
            company_name=company_name,
            other_description=other_description,
            contact_email=contact_email, contact_phone=contact_phone,
        )
    except SubscriberError as e:
        return _rerender(str(e))

    db.commit()
    audit_service.record(
        db,
        action="subscriber_updated",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber.SubscriberID,
        details=f"name={subscriber.Name}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/subscribers/{subscriber_id}/deactivate")
def deactivate_subscriber_route(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
    new_status: Annotated[str, Form()] = "archived",
):
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)

    try:
        subscriber_service.deactivate_subscriber(db, subscriber, new_status=new_status)
    except SubscriberError:
        return RedirectResponse(
            f"/admin/subscribers/{subscriber_id}/edit",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    db.commit()
    audit_service.record(
        db,
        action="subscriber_deactivated",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber_id,
        details=f"new_status={new_status}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Trial provisioning routes
# ---------------------------------------------------------------------------

@router.get("/admin/subscribers/{subscriber_id}/trial", response_class=HTMLResponse)
def trial_provision_form(
    subscriber_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.trial_config import SubscriberTrialConfig
    from app.models.station import Station
    from app.services.trial_service import get_all_service_consumed, DEFAULT_ALLOCATIONS

    sub = db.get(Subscriber, subscriber_id)
    if not sub:
        return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)

    trial_cfg = db.get(SubscriberTrialConfig, subscriber_id)
    stations  = db.query(Station).filter(Station.IsActive == True).order_by(Station.StationName).all()  # noqa: E712
    consumed  = get_all_service_consumed(db, subscriber_id) if trial_cfg else {}

    return templates.TemplateResponse(
        request=request,
        name="admin/trial_provision.html",
        context={
            "user": user,
            "sub": sub,
            "trial_cfg": trial_cfg,
            "stations": stations,
            "consumed": consumed,
            "defaults": DEFAULT_ALLOCATIONS,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/admin/subscribers/{subscriber_id}/trial")
def trial_provision_submit(
    subscriber_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    station_id:        int     = Form(...),
    max_commercial:    float   = Form(default=60.0),
    max_song:          float   = Form(default=40.0),
    max_word:          float   = Form(default=40.0),
    max_transcription: float   = Form(default=28.0),
    duration_days:     int     = Form(default=7),
):
    from decimal import Decimal as D
    from app.services.trial_service import provision_trial
    from app.services import token_service

    sub = db.get(Subscriber, subscriber_id)
    if not sub:
        return RedirectResponse("/admin/subscribers", status_code=status.HTTP_303_SEE_OTHER)

    cfg = provision_trial(
        db=db,
        subscriber_id=subscriber_id,
        station_id=station_id,
        created_by_user_id=user.UserID,
        max_commercial=D(str(max_commercial)),
        max_song=D(str(max_song)),
        max_word=D(str(max_word)),
        max_transcription=D(str(max_transcription)),
        duration_days=duration_days,
    )

    # Credit trial tokens if account is empty
    from app.services.trial_service import TRIAL_TOTAL_CREDITS
    total = D(str(max_commercial)) + D(str(max_song)) + D(str(max_word)) + D(str(max_transcription))
    balance = token_service.get_balance(db, subscriber_id)
    if balance < total:
        token_service.credit(
            db,
            subscriber_id=subscriber_id,
            amount=total - balance,
            description="Trial credit allocation",
            created_by_user_id=user.UserID,
            source="admin",
        )

    db.commit()
    audit_service.record(
        db,
        action="trial_provisioned",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber_id,
        details=f"station={station_id} expires={cfg.TrialExpiresAt:%Y-%m-%d} total={total}",
        ip_address=request.client.host if request.client else "unknown",
    )
    return RedirectResponse(
        f"/admin/subscribers/{subscriber_id}/trial",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/subscribers/{subscriber_id}/trial/expire")
def trial_expire(
    subscriber_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.services.trial_service import expire_trial
    expire_trial(db, subscriber_id)
    db.commit()
    audit_service.record(
        db,
        action="trial_expired",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber_id,
        ip_address=request.client.host if request.client else "unknown",
    )
    return RedirectResponse(
        f"/admin/subscribers/{subscriber_id}/trial",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ---------------------------------------------------------------------------
# Internal Users
# ---------------------------------------------------------------------------

@router.get("/admin/users", response_class=HTMLResponse)
def list_internal_users(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.user import UserType
    users = (
        db.query(User)
        .filter(User.UserType == UserType.INTERNAL)
        .order_by(User.FullName)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/internal_users.html",
        context={"user": user, "users": users},
    )


@router.get("/admin/users/new", response_class=HTMLResponse)
def new_internal_user_form(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="admin/internal_user_form.html",
        context={"user": user, "edit_user": None, "error": None, "form": {}},
    )


@router.post("/admin/users")
def create_internal_user(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    full_name:        Annotated[str, Form()],
    email:            Annotated[str, Form()],
    password:         Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
    can_download:     Annotated[str, Form()] = "off",
):
    from app.models.user import UserType
    from app.auth.password import hash_password

    def _rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/internal_user_form.html",
            context={
                "user": user, "edit_user": None, "error": error,
                "form": {"full_name": full_name, "email": email},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not full_name.strip():
        return _rerender("Full name is required.")
    if not email.strip():
        return _rerender("Email is required.")
    if len(password) < 8:
        return _rerender("Password must be at least 8 characters.")
    if password != password_confirm:
        return _rerender("Passwords do not match.")

    existing = db.query(User).filter(User.Email == email.strip().lower()).first()
    if existing:
        return _rerender(f"Email {email} is already registered.")

    new_user = User(
        Email=email.strip().lower(),
        PasswordHash=hash_password(password),
        FullName=full_name.strip(),
        UserType=UserType.INTERNAL,
        SubscriberID=None,
        IsActive=True,
        EmailVerified=True,
        CanDownload=can_download == "on",
        CreatedByUserID=user.UserID,
    )
    db.add(new_user)
    db.commit()
    audit_service.record(
        db,
        action="internal_user_created",
        actor_user_id=user.UserID,
        details=f"email={new_user.Email} name={new_user.FullName}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
def edit_internal_user_form(
    user_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.user import UserType
    edit_user = db.get(User, user_id)
    if not edit_user or edit_user.UserType != UserType.INTERNAL:
        return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="admin/internal_user_form.html",
        context={"user": user, "edit_user": edit_user, "error": None, "form": {}},
    )


@router.post("/admin/users/{user_id}")
def update_internal_user(
    user_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    full_name:        Annotated[str, Form()],
    email:            Annotated[str, Form()],
    password:         Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
    can_download:     Annotated[str, Form()] = "off",
    is_active:        Annotated[str, Form()] = "off",
):
    from app.models.user import UserType
    from app.auth.password import hash_password

    edit_user = db.get(User, user_id)
    if not edit_user or edit_user.UserType != UserType.INTERNAL:
        return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    def _rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="admin/internal_user_form.html",
            context={
                "user": user, "edit_user": edit_user, "error": error,
                "form": {"full_name": full_name, "email": email},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not full_name.strip():
        return _rerender("Full name is required.")
    if not email.strip():
        return _rerender("Email is required.")

    # Check email uniqueness (excluding self)
    existing = db.query(User).filter(
        User.Email == email.strip().lower(),
        User.UserID != user_id,
    ).first()
    if existing:
        return _rerender(f"Email {email} is already registered.")

    # Password change only if provided
    if password:
        if len(password) < 8:
            return _rerender("Password must be at least 8 characters.")
        if password != password_confirm:
            return _rerender("Passwords do not match.")
        edit_user.PasswordHash = hash_password(password)
        edit_user.SessionVersion += 1  # invalidate existing sessions

    edit_user.FullName    = full_name.strip()
    edit_user.Email       = email.strip().lower()
    edit_user.CanDownload = can_download == "on"
    edit_user.IsActive    = is_active == "on"
    db.commit()

    audit_service.record(
        db,
        action="internal_user_updated",
        actor_user_id=user.UserID,
        details=f"user_id={user_id} email={edit_user.Email}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/users/{user_id}/deactivate")
def deactivate_internal_user(
    user_id: int,
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.user import UserType

    # Prevent self-deactivation
    if user_id == user.UserID:
        return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    edit_user = db.get(User, user_id)
    if not edit_user or edit_user.UserType != UserType.INTERNAL:
        return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)

    edit_user.IsActive = False
    edit_user.SessionVersion += 1
    db.commit()

    audit_service.record(
        db,
        action="internal_user_deactivated",
        actor_user_id=user.UserID,
        details=f"user_id={user_id} email={edit_user.Email}",
        ip_address=_client_ip(request),
    )
    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# Transcription retry endpoint
# ---------------------------------------------------------------------------

@router.post("/admin/transcriptions/retry-pending")
def retry_pending_transcriptions(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    """Re-run process_request() on all pending TranscriptionRequests."""
    from app.services.transcription_service import retry_pending_requests
    result = retry_pending_requests(db)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Manual midnight tasks trigger (for testing without waiting for 00:01)
# ---------------------------------------------------------------------------

@router.post("/admin/run-midnight-tasks")
def run_midnight_tasks_now(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    """Manually trigger all midnight tasks — campaign expiry, station sync, transcription retry."""
    from app.services.station_scheduler import run_midnight_tasks
    result = run_midnight_tasks(db)
    return JSONResponse(result)
