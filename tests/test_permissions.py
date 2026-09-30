"""Tests for the permission predicates (v0.3 -- Subscriber model)."""

import pytest
from unittest.mock import MagicMock

from app.auth.permissions import (
    can_access_subscriber,
    enforce_active,
    enforce_admin_within_tenant,
    enforce_internal,
    is_admin_within_tenant,
    is_internal,
)
from app.models.user import User, UserType
from fastapi import HTTPException


def _user(user_type: str, subscriber_id=None, is_active=True) -> User:
    u = MagicMock(spec=User)
    u.UserType = user_type
    u.user_type = UserType(user_type)
    u.SubscriberID = subscriber_id
    u.IsActive = is_active
    return u


def test_is_internal_true():
    assert is_internal(_user("internal")) is True


def test_is_internal_false():
    assert is_internal(_user("subscriber_admin", 1)) is False


def test_is_admin_within_tenant_subscriber_admin():
    assert is_admin_within_tenant(_user("subscriber_admin", 1)) is True


def test_is_admin_within_tenant_subscriber_user():
    assert is_admin_within_tenant(_user("subscriber_user", 1)) is False


def test_is_admin_within_tenant_internal():
    assert is_admin_within_tenant(_user("internal")) is True


def test_can_access_subscriber_internal():
    u = _user("internal")
    assert can_access_subscriber(u, 42) is True


def test_can_access_subscriber_own():
    u = _user("subscriber_admin", subscriber_id=7)
    assert can_access_subscriber(u, 7) is True


def test_can_access_subscriber_other():
    u = _user("subscriber_user", subscriber_id=7)
    assert can_access_subscriber(u, 99) is False


def test_enforce_internal_passes():
    enforce_internal(_user("internal"))


def test_enforce_internal_raises():
    with pytest.raises(HTTPException) as exc_info:
        enforce_internal(_user("subscriber_admin", 1))
    assert exc_info.value.status_code == 403


def test_enforce_admin_raises_for_user():
    with pytest.raises(HTTPException):
        enforce_admin_within_tenant(_user("subscriber_user", 1))


def test_enforce_active_raises():
    with pytest.raises(HTTPException):
        enforce_active(_user("internal", is_active=False))


def test_enforce_active_passes():
    enforce_active(_user("internal", is_active=True))
