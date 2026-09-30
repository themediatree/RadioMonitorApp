"""
Invitation routes (v0.3).

Admin-facing (require admin-within-tenant: internal or subscriber_admin):
    GET  /admin/invitations          -- list invitations the user may see
    GET  /admin/invitations/new      -- form to create one
    POST /admin/invitations          -- create + "send" (logs link in dev)

Public (no auth):
    GET  /accept-invite?token=...    -- validate token, show set-password form
    POST /accept-invite              -- create the user, mark accepted, auto-login
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth.password import (
    PasswordPolicyError,
    hash_password,
    validate_password,
)
from app.auth.session import issue_token_for, set_session_cookie
from app.database import get_db
from app.deps import get_current_user, require_admin_within_tenant  # noqa: F401
from app.models.invitation import UserInvitation
from app.models.subscriber import Subscriber
from app.models.user import User, UserType
from app.services import audit_service, email_service, invitation_service
from app.services.invitation_service import InvitationError
from app.templating import templates

router = APIRouter(tags=["invitations"])


def _client_ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _visible_invitations(db: Session, user: User) -> list[UserInvitation]:
    """Invitations the current user may see."""
    q = db.query(UserInvitation)
    if user.user_type == UserType.INTERNAL:
        pass
    elif user.user_type == UserType.SUBSCRIBER_ADMIN:
        q = q.filter(UserInvitation.SubscriberID == user.SubscriberID)
    else:
        return []
    return q.order_by(UserInvitation.CreatedAt.desc()).all()


@router.get("/admin/invitations", response_class=HTMLResponse)
def list_invitations(
    request: Request,
    user: Annotated[User, Depends(require_admin_within_tenant)],
    db: Annotated[Session, Depends(get_db)],
):
    invitations = _visible_invitations(db, user)
    return templates.TemplateResponse(
        request=request,
        name="admin/invitations.html",
        context={"user": user, "invitations": invitations},
    )


@router.get("/admin/invitations/new", response_class=HTMLResponse)
def new_invitation_form(
    request: Request,
    user: Annotated[User, Depends(require_admin_within_tenant)],
    db: Annotated[Session, Depends(get_db)],
):
    types = [t.value for t in invitation_service.invitable_types(user)]
    # Internal users pick which Subscriber the invite is for.
    subscribers = (
        db.query(Subscriber).order_by(Subscriber.Name).all()
        if user.user_type == UserType.INTERNAL
        else []
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/invitation_new.html",
        context={
            "user": user,
            "types": types,
            "subscribers": subscribers,
            "error": None,
            "form": {},
        },
    )


@router.post("/admin/invitations")
def create_invitation(
    request: Request,
    user: Annotated[User, Depends(require_admin_within_tenant)],
    db: Annotated[Session, Depends(get_db)],
    email: Annotated[str, Form()],
    user_type: Annotated[str, Form()],
    subscriber_id: Annotated[str, Form()] = "",
):
    def _parse_optional_int(raw: str) -> Optional[int]:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    sub_id_int = _parse_optional_int(subscriber_id)

    def _rerender(error: str):
        types = [t.value for t in invitation_service.invitable_types(user)]
        subs = (
            db.query(Subscriber).order_by(Subscriber.Name).all()
            if user.user_type == UserType.INTERNAL
            else []
        )
        return templates.TemplateResponse(
            request=request,
            name="admin/invitation_new.html",
            context={
                "user": user, "types": types,
                "subscribers": subs,
                "error": error,
                "form": {"email": email, "user_type": user_type,
                         "subscriber_id": sub_id_int},
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        invitee_type = UserType(user_type)
    except ValueError:
        return _rerender(f"Unknown user type: {user_type!r}")

    try:
        invitation = invitation_service.create_invitation(
            db,
            inviter=user,
            email=email,
            invitee_type=invitee_type,
            requested_subscriber_id=sub_id_int,
        )
    except InvitationError as e:
        return _rerender(str(e))

    db.commit()

    email_service.send_invitation_email(
        to_email=invitation.Email,
        token=invitation.Token,
        invited_by_name=user.display_name,
        user_type=invitation.UserType,
    )

    audit_service.record(
        db,
        action="user_invited",
        actor_user_id=user.UserID,
        target_subscriber_id=invitation.SubscriberID,
        details=f"email={invitation.Email} type={invitation.UserType}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    return RedirectResponse(
        url="/admin/invitations",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/accept-invite", response_class=HTMLResponse)
def accept_invite_form(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: str = "",
):
    try:
        invitation = invitation_service.get_valid_invitation(db, token)
    except InvitationError as e:
        return templates.TemplateResponse(
            request=request,
            name="auth/accept_invite.html",
            context={"error": str(e), "valid": False, "token": token,
                     "email": None},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return templates.TemplateResponse(
        request=request,
        name="auth/accept_invite.html",
        context={"error": None, "valid": True, "token": token,
                 "email": invitation.Email},
    )


@router.post("/accept-invite")
def accept_invite_submit(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Form()],
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
    full_name: Annotated[str, Form()] = "",
):
    def _rerender(error: str, email: Optional[str], valid: bool = True):
        return templates.TemplateResponse(
            request=request,
            name="auth/accept_invite.html",
            context={"error": error, "valid": valid, "token": token,
                     "email": email, "full_name": full_name},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        invitation = invitation_service.get_valid_invitation(db, token)
    except InvitationError as e:
        return _rerender(str(e), email=None, valid=False)

    if password != password_confirm:
        return _rerender("Passwords do not match.", email=invitation.Email)

    try:
        validate_password(password)
    except PasswordPolicyError as e:
        return _rerender(str(e), email=invitation.Email)

    try:
        new_user = invitation_service.accept_invitation(
            db,
            invitation=invitation,
            full_name=full_name.strip() or None,
            password_hash=hash_password(password),
        )
    except InvitationError as e:
        return _rerender(str(e), email=invitation.Email, valid=False)

    # Activate the Subscriber now that email is verified and password is set.
    if new_user.SubscriberID:
        from app.models.subscriber import Subscriber
        subscriber = db.get(Subscriber, new_user.SubscriberID)
        if subscriber and subscriber.SubscriberStatus == "pending":
            subscriber.SubscriberStatus = "active"
        # Ensure a token account exists so admin can pre-credit before first registration.
        from app.services.token_service import get_or_create_account
        get_or_create_account(db, new_user.SubscriberID)
        # Provision trial tokens if the plan has a trial allocation.
        if subscriber:
            from app.services.token_service import provision_trial_tokens
            provision_trial_tokens(db, new_user.SubscriberID, subscriber.SubscriptionPlan)

    db.commit()

    audit_service.record(
        db,
        action="user_invitation_accepted",
        actor_user_id=new_user.UserID,
        target_user_id=new_user.UserID,
        target_subscriber_id=new_user.SubscriberID,
        details=f"email={new_user.Email} type={new_user.UserType}",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    token_str, _ = issue_token_for(new_user, db)
    db.commit()
    response = RedirectResponse(url="/dashboard",
                                status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(response, token_str)
    return response
