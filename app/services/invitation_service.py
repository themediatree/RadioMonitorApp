"""
Invitation business logic (v0.3).

Authorization matrix:
    internal          -> may invite subscriber_admin / subscriber_user into
                         ANY Subscriber. Must supply the target SubscriberID.
    subscriber_admin  -> may invite subscriber_admin / subscriber_user, ONLY
                         into their own SubscriberID. Cannot set a different
                         Subscriber, cannot invite internal users.
    everyone else     -> may not invite.

The Subscriber binding for a subscriber_admin is ALWAYS taken from the
inviter, never from form input -- prevents escalation by inviting into
someone else's Subscriber.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.invitation import UserInvitation
from app.models.user import User, UserType


INVITATION_TTL_DAYS = 7
_TOKEN_BYTES = 32


class InvitationError(Exception):
    """Raised when an invitation request is invalid or not permitted."""


def _utc_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def generate_token() -> str:
    """Cryptographically strong, URL-safe token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


# Which invitee types each inviter type may create.
_ALLOWED_INVITE_TYPES: dict[UserType, set[UserType]] = {
    UserType.INTERNAL: {UserType.SUBSCRIBER_ADMIN, UserType.SUBSCRIBER_USER},
    UserType.SUBSCRIBER_ADMIN: {UserType.SUBSCRIBER_ADMIN, UserType.SUBSCRIBER_USER},
}


def can_invite(inviter: User) -> bool:
    return inviter.user_type in _ALLOWED_INVITE_TYPES


def invitable_types(inviter: User) -> list[UserType]:
    return sorted(
        _ALLOWED_INVITE_TYPES.get(inviter.user_type, set()), key=lambda t: t.value
    )


def resolve_invitation_target(
    inviter: User,
    invitee_type: UserType,
    *,
    requested_subscriber_id: Optional[int] = None,
) -> int:
    """
    Decide which SubscriberID the invitation binds to, enforcing the matrix.
    For subscriber_admin, binding is forced from the inviter (requested value
    ignored to prevent escalation). For internal, requested value is required.
    Raises InvitationError on any violation.
    """
    if not can_invite(inviter):
        raise InvitationError("You are not permitted to invite users.")

    allowed = _ALLOWED_INVITE_TYPES[inviter.user_type]
    if invitee_type not in allowed:
        raise InvitationError(
            f"You may not invite a user of type '{invitee_type.value}'."
        )

    if inviter.user_type == UserType.SUBSCRIBER_ADMIN:
        if inviter.SubscriberID is None:
            raise InvitationError("Inviter has no Subscriber association.")
        return inviter.SubscriberID

    # Internal: must supply a target Subscriber.
    if not requested_subscriber_id:
        raise InvitationError("A Subscriber must be selected.")
    return requested_subscriber_id


def create_invitation(
    db: Session,
    *,
    inviter: User,
    email: str,
    invitee_type: UserType,
    requested_subscriber_id: Optional[int] = None,
) -> UserInvitation:
    """Create and persist an invitation. Caller commits."""
    from app.utils.email_validator import validate_email, EmailValidationError
    try:
        email = validate_email(email)
    except EmailValidationError as e:
        raise InvitationError(str(e)) from e

    subscriber_id = resolve_invitation_target(
        inviter,
        invitee_type,
        requested_subscriber_id=requested_subscriber_id,
    )

    existing_user = db.query(User).filter(User.Email == email).one_or_none()
    if existing_user is not None:
        raise InvitationError("A user with that email already exists.")

    open_invite = (
        db.query(UserInvitation)
        .filter(
            UserInvitation.Email == email,
            UserInvitation.AcceptedAt.is_(None),
            UserInvitation.ExpiresAt > _utc_naive(),
        )
        .one_or_none()
    )
    if open_invite is not None:
        raise InvitationError("An invitation for that email is already pending.")

    invitation = UserInvitation(
        Email=email,
        Token=generate_token(),
        UserType=invitee_type.value,
        SubscriberID=subscriber_id,
        InvitedByUserID=inviter.UserID,
        ExpiresAt=_utc_naive() + timedelta(days=INVITATION_TTL_DAYS),
    )
    db.add(invitation)
    db.flush()
    return invitation


def get_valid_invitation(db: Session, token: str) -> UserInvitation:
    if not token:
        raise InvitationError("Missing invitation token.")

    invitation = (
        db.query(UserInvitation)
        .filter(UserInvitation.Token == token)
        .one_or_none()
    )
    if invitation is None:
        raise InvitationError("This invitation link is not valid.")
    if invitation.is_accepted:
        raise InvitationError("This invitation has already been used.")
    if invitation.is_expired:
        raise InvitationError("This invitation has expired. Ask for a new one.")
    return invitation


def accept_invitation(
    db: Session,
    *,
    invitation: UserInvitation,
    full_name: Optional[str],
    password_hash: str,
) -> User:
    """Create the User from an invitation and mark the invitation accepted."""
    if invitation.is_accepted:
        raise InvitationError("This invitation has already been used.")
    if invitation.is_expired:
        raise InvitationError("This invitation has expired.")

    existing = db.query(User).filter(User.Email == invitation.Email).one_or_none()
    if existing is not None:
        raise InvitationError("A user with that email already exists.")

    user = User(
        Email=invitation.Email,
        PasswordHash=password_hash,
        FullName=(full_name or None),
        UserType=invitation.UserType,
        SubscriberID=invitation.SubscriberID,
        IsActive=True,
        EmailVerified=True,
        CreatedByUserID=invitation.InvitedByUserID,
    )
    db.add(user)
    invitation.AcceptedAt = _utc_naive()
    db.flush()
    return user
