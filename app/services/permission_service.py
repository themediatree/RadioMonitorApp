"""
app/services/permission_service.py

Per-user station restrictions and download rights, within a Subscriber.

Core rule: a user is either UNRESTRICTED (HasStationRestrictions=False,
sees everything their Subscriber can see -- this is the default for every
existing and newly-created user) or RESTRICTED (HasStationRestrictions=True,
UserStationPermission is the explicit allow-list, which may be empty).

Only a subscriber_admin can set restrictions, only for users within their
own Subscriber. Internal staff and subscriber_admins themselves are never
restricted by this mechanism -- it only applies to subscriber_user accounts.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User, UserStationPermission, UserType


class PermissionError(Exception):
    pass


def get_allowed_station_ids(db: Session, user: User) -> Optional[set[int]]:
    """
    Returns the set of StationIDs this user may access, or None if the user
    is unrestricted (no filtering should be applied -- caller sees
    everything their Subscriber/scope already allows).

    internal and subscriber_admin users are always unrestricted by this
    mechanism, regardless of HasStationRestrictions (which only meaningfully
    applies to subscriber_user accounts created/configured by their admin).
    """
    if user.user_type in (UserType.INTERNAL, UserType.SUBSCRIBER_ADMIN):
        return None
    if not user.HasStationRestrictions:
        return None
    rows = (
        db.query(UserStationPermission.StationID)
        .filter(UserStationPermission.UserID == user.UserID)
        .all()
    )
    return {r[0] for r in rows}


def can_download(user: User) -> bool:
    """internal and subscriber_admin always can; subscriber_user follows CanDownload."""
    if user.user_type in (UserType.INTERNAL, UserType.SUBSCRIBER_ADMIN):
        return True
    return bool(user.CanDownload)


def _assert_admin_can_manage(admin: User, target_user: User) -> None:
    """
    Only a subscriber_admin may set permissions, and only for a user within
    their own Subscriber. Internal staff never call this for a tenant's
    users (they have their own separate admin tooling, unaffected by this).
    """
    if admin.user_type != UserType.SUBSCRIBER_ADMIN:
        raise PermissionError("Only a Subscriber admin can manage user permissions.")
    if target_user.SubscriberID != admin.SubscriberID:
        raise PermissionError("You can only manage users within your own organisation.")
    if target_user.user_type != UserType.SUBSCRIBER_USER:
        raise PermissionError("Permissions can only be set for standard users, not admins.")


def set_station_restrictions(
    db: Session,
    admin: User,
    target_user: User,
    station_ids: list[int],
) -> None:
    """
    Sets the explicit station allow-list for target_user and flips
    HasStationRestrictions=True. Passing an empty list means "restricted to
    nothing" (deliberate full lockout), not "unrestricted" -- use
    clear_station_restrictions() to remove restrictions entirely.
    """
    _assert_admin_can_manage(admin, target_user)

    db.query(UserStationPermission).filter(
        UserStationPermission.UserID == target_user.UserID
    ).delete()

    for sid in set(station_ids):
        db.add(UserStationPermission(UserID=target_user.UserID, StationID=sid))

    target_user.HasStationRestrictions = True
    db.flush()


def clear_station_restrictions(db: Session, admin: User, target_user: User) -> None:
    """Removes all restrictions -- target_user becomes fully unrestricted again."""
    _assert_admin_can_manage(admin, target_user)

    db.query(UserStationPermission).filter(
        UserStationPermission.UserID == target_user.UserID
    ).delete()
    target_user.HasStationRestrictions = False
    db.flush()


def set_can_download(db: Session, admin: User, target_user: User, allowed: bool) -> None:
    _assert_admin_can_manage(admin, target_user)
    target_user.CanDownload = allowed
    db.flush()


def get_team_members(db: Session, admin: User) -> list[User]:
    """All users within admin's own Subscriber (for the Team management page)."""
    if admin.user_type != UserType.SUBSCRIBER_ADMIN:
        raise PermissionError("Only a Subscriber admin can view the team page.")
    return (
        db.query(User)
        .filter(User.SubscriberID == admin.SubscriberID)
        .order_by(User.UserType.desc(), User.FullName, User.Email)
        .all()
    )


def visible_stations(db: Session, user: User):
    """
    Active stations the user may see/select from, respecting their station
    restrictions if any. Excludes StationID 6 (the internal 'generic'
    catch-all, never a real broadcast station).

    Shared by every route that renders a station picker (registration,
    songs, words, transcriptions, detections) so the restriction is
    enforced consistently everywhere a station list is shown, not just in
    the underlying scoping filters.
    """
    from app.models.station import Station

    q = (
        db.query(Station)
        .filter(Station.IsActive == True)  # noqa: E712
        .filter(Station.StationID != 6)
    )
    allowed = get_allowed_station_ids(db, user)
    if allowed is not None:
        if not allowed:
            return []
        q = q.filter(Station.StationID.in_(allowed))
    return q.order_by(Station.StationName).all()


def get_user_station_ids(db: Session, target_user: User) -> set[int]:
    """Current explicit allow-list for a user, regardless of whether it's active."""
    rows = (
        db.query(UserStationPermission.StationID)
        .filter(UserStationPermission.UserID == target_user.UserID)
        .all()
    )
    return {r[0] for r in rows}
