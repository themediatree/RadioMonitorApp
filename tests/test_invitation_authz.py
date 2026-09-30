"""Invitation authorization tests (v0.3 -- Subscriber model)."""

import pytest
from unittest.mock import MagicMock

from app.models.user import User, UserType
from app.services.invitation_service import (
    InvitationError,
    can_invite,
    invitable_types,
    resolve_invitation_target,
)


def _user(user_type: str, subscriber_id=None) -> User:
    u = MagicMock(spec=User)
    u.UserType = user_type
    u.user_type = UserType(user_type)
    u.SubscriberID = subscriber_id
    return u


class TestCanInvite:
    def test_internal_can_invite(self):
        assert can_invite(_user("internal")) is True

    def test_subscriber_admin_can_invite(self):
        assert can_invite(_user("subscriber_admin", 1)) is True

    def test_subscriber_user_cannot_invite(self):
        assert can_invite(_user("subscriber_user", 1)) is False


class TestInvitableTypes:
    def test_internal_can_invite_both_sub_types(self):
        types = invitable_types(_user("internal"))
        assert UserType.SUBSCRIBER_ADMIN in types
        assert UserType.SUBSCRIBER_USER in types

    def test_subscriber_admin_can_invite_both_sub_types(self):
        types = invitable_types(_user("subscriber_admin", 1))
        assert UserType.SUBSCRIBER_ADMIN in types
        assert UserType.SUBSCRIBER_USER in types


class TestResolveTarget:
    def test_subscriber_admin_forced_to_own_subscriber(self):
        u = _user("subscriber_admin", subscriber_id=7)
        sid = resolve_invitation_target(u, UserType.SUBSCRIBER_USER)
        assert sid == 7

    def test_subscriber_admin_ignores_requested(self):
        u = _user("subscriber_admin", subscriber_id=7)
        sid = resolve_invitation_target(
            u, UserType.SUBSCRIBER_USER, requested_subscriber_id=99
        )
        assert sid == 7  # forced to their own

    def test_internal_uses_requested_subscriber(self):
        u = _user("internal")
        sid = resolve_invitation_target(
            u, UserType.SUBSCRIBER_ADMIN, requested_subscriber_id=42
        )
        assert sid == 42

    def test_internal_without_subscriber_raises(self):
        u = _user("internal")
        with pytest.raises(InvitationError, match="Subscriber"):
            resolve_invitation_target(u, UserType.SUBSCRIBER_USER)

    def test_subscriber_admin_no_subscriber_raises(self):
        u = _user("subscriber_admin", subscriber_id=None)
        with pytest.raises(InvitationError):
            resolve_invitation_target(u, UserType.SUBSCRIBER_USER)

    def test_uninvitable_user_type_raises(self):
        u = _user("subscriber_user", subscriber_id=1)
        with pytest.raises(InvitationError):
            resolve_invitation_target(u, UserType.SUBSCRIBER_ADMIN)
