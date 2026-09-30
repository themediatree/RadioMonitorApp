"""
Role and permission helpers (v0.3).

Tiny composable predicates + enforcers used by route handlers via the
dependencies in app/deps.py:

    @router.get("/admin", dependencies=[Depends(require_internal)])
    def admin_only(...): ...

Tenancy filtering (e.g. "show only this Subscriber's detections") lives in
app/services/scoping.py; the predicates here only handle role-level access.
"""

from fastapi import HTTPException, status

from app.models.user import User, UserType


# ---------------------------------------------------------------------------
# Predicates -- no exceptions, just bool
# ---------------------------------------------------------------------------
def is_internal(user: User) -> bool:
    return user.user_type == UserType.INTERNAL


def is_admin_within_tenant(user: User) -> bool:
    """Admin within their own Subscriber, or any internal user."""
    return user.user_type.is_internal or user.user_type.is_admin_tier


def can_access_subscriber(user: User, subscriber_id: int) -> bool:
    """
    Whether the user can see data belonging to the given Subscriber.
        - internal:        yes, always
        - subscriber_*:    only their own SubscriberID
    """
    if user.user_type.is_internal:
        return True
    return user.SubscriberID == subscriber_id


# ---------------------------------------------------------------------------
# Enforcers -- raise HTTPException on failure (for use in route deps)
# ---------------------------------------------------------------------------
def enforce_internal(user: User) -> None:
    if not is_internal(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal access required.",
        )


def enforce_admin_within_tenant(user: User) -> None:
    if not is_admin_within_tenant(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )


def enforce_active(user: User) -> None:
    if not user.IsActive:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated.",
        )
