"""End-to-end invitation flow tests (v0.3)."""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import app.database as db_module


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True,
    )
    db_module.engine = eng
    db_module.SessionLocal = sessionmaker(bind=eng, future=True, expire_on_commit=False)
    import app.models  # noqa
    db_module.Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def db(engine):
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.subscriber import Subscriber
    from app.models.user import User
    with Session(engine, expire_on_commit=False) as session:
        session.add(SubscriptionPlanConfig(PlanCode="std", DisplayName="Standard", IsActive=True))
        sub = Subscriber(Name="Test Co", Slug="test-co", SubscriberType="Advertiser",
                         SubscriptionPlan="std", SubscriberStatus="active")
        session.add(sub)
        session.flush()
        admin = User(Email="admin@test.com", PasswordHash="x",
                     UserType="internal", IsActive=True)
        sub_admin = User(Email="sub_admin@test.com", PasswordHash="x",
                         UserType="subscriber_admin", SubscriberID=sub.SubscriberID,
                         IsActive=True)
        session.add_all([admin, sub_admin])
        session.commit()
        session._sub_id = sub.SubscriberID
        session._admin_id = admin.UserID
        session._sub_admin_id = sub_admin.UserID
    with Session(engine, expire_on_commit=False) as session:
        session._sub_id = sub.SubscriberID
        session._admin_id = admin.UserID
        session._sub_admin_id = sub_admin.UserID
        yield session


class TestInvitationCreate:
    def test_internal_can_invite_to_any_subscriber(self, db):
        from app.models.user import User, UserType
        from app.services.invitation_service import create_invitation
        inviter = db.get(User, db._admin_id)
        inv = create_invitation(db, inviter=inviter, email="new1@test.com",
                                invitee_type=UserType.SUBSCRIBER_USER,
                                requested_subscriber_id=db._sub_id)
        db.commit()
        assert inv.SubscriberID == db._sub_id
        assert inv.UserType == "subscriber_user"

    def test_subscriber_admin_invite_forced_to_own(self, db):
        from app.models.user import User, UserType
        from app.services.invitation_service import create_invitation
        inviter = db.get(User, db._sub_admin_id)
        inv = create_invitation(db, inviter=inviter, email="new2@test.com",
                                invitee_type=UserType.SUBSCRIBER_USER)
        db.commit()
        assert inv.SubscriberID == db._sub_id

    def test_duplicate_email_fails(self, db):
        from app.models.user import User, UserType
        from app.services.invitation_service import create_invitation, InvitationError
        inviter = db.get(User, db._admin_id)
        # "new1@test.com" already invited above.
        with pytest.raises(InvitationError, match="pending"):
            create_invitation(db, inviter=inviter, email="new1@test.com",
                              invitee_type=UserType.SUBSCRIBER_USER,
                              requested_subscriber_id=db._sub_id)


class TestInvitationAccept:
    def test_accept_creates_user_with_subscriber(self, db):
        from app.models.user import User, UserType
        from app.services.invitation_service import (
            create_invitation, get_valid_invitation, accept_invitation,
        )
        inviter = db.get(User, db._admin_id)
        inv = create_invitation(db, inviter=inviter, email="accept@test.com",
                                invitee_type=UserType.SUBSCRIBER_ADMIN,
                                requested_subscriber_id=db._sub_id)
        db.commit()

        inv = get_valid_invitation(db, inv.Token)
        new_user = accept_invitation(db, invitation=inv,
                                     full_name="Test User", password_hash="hashed")
        db.commit()
        assert new_user.SubscriberID == db._sub_id
        assert new_user.UserType == "subscriber_admin"
        assert new_user.EmailVerified is True
        assert inv.AcceptedAt is not None

    def test_accept_twice_fails(self, db):
        from app.services.invitation_service import get_valid_invitation, InvitationError
        from app.models.invitation import UserInvitation
        # Find the already-accepted invitation.
        inv = db.query(UserInvitation).filter(
            UserInvitation.Email == "accept@test.com"
        ).first()
        with pytest.raises(InvitationError, match="already"):
            get_valid_invitation(db, inv.Token)
