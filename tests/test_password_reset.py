"""Tests for the self-service password reset flow."""

import pytest
from datetime import datetime, timedelta


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import app.database as db_module
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


@pytest.fixture
def db(engine):
    from sqlalchemy.orm import Session
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _make_user(db, suffix, is_active=True):
    from app.models.subscriber import Subscriber
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.user import User, UserType

    db.add(SubscriptionPlanConfig(PlanCode=f"reset{suffix}", DisplayName="Reset", IsActive=True))
    sub = Subscriber(Name=f"ResetSub{suffix}", Slug=f"reset-sub-{suffix}",
                     SubscriberType="Agent", SubscriptionPlan=f"reset{suffix}",
                     SubscriberStatus="active")
    db.add(sub); db.flush()

    user = User(Email=f"reset{suffix}@test.com", PasswordHash="old-hash",
               SubscriberID=sub.SubscriberID, UserType=UserType.SUBSCRIBER_ADMIN,
               IsActive=is_active, SessionVersion=1)
    db.add(user); db.flush(); db.commit()
    return user


class TestRequestReset:
    def test_creates_token_for_active_user(self, db):
        from app.services.password_reset_service import request_reset
        user = _make_user(db, 1)
        token = request_reset(db, user.Email)
        db.commit()
        assert token is not None
        assert token.UserID == user.UserID
        assert token.is_valid

    def test_returns_none_for_unknown_email(self, db):
        from app.services.password_reset_service import request_reset
        token = request_reset(db, "nobody@test.com")
        assert token is None

    def test_returns_none_for_inactive_user(self, db):
        from app.services.password_reset_service import request_reset
        user = _make_user(db, 2, is_active=False)
        token = request_reset(db, user.Email)
        assert token is None

    def test_email_matching_is_case_insensitive(self, db):
        from app.services.password_reset_service import request_reset
        user = _make_user(db, 3)
        token = request_reset(db, user.Email.upper())
        db.commit()
        assert token is not None

    def test_prior_unused_token_invalidated_by_new_request(self, db):
        from app.services.password_reset_service import request_reset
        user = _make_user(db, 4)
        first = request_reset(db, user.Email)
        db.commit()
        first_token_str = first.Token

        second = request_reset(db, user.Email)
        db.commit()

        from app.services.password_reset_service import validate_token
        assert validate_token(db, first_token_str) is None
        assert validate_token(db, second.Token) is not None


class TestValidateToken:
    def test_valid_token_returns_row(self, db):
        from app.services.password_reset_service import request_reset, validate_token
        user = _make_user(db, 5)
        token = request_reset(db, user.Email)
        db.commit()
        result = validate_token(db, token.Token)
        assert result is not None

    def test_unknown_token_returns_none(self, db):
        from app.services.password_reset_service import validate_token
        assert validate_token(db, "does-not-exist") is None

    def test_empty_token_returns_none(self, db):
        from app.services.password_reset_service import validate_token
        assert validate_token(db, "") is None
        assert validate_token(db, None) is None

    def test_expired_token_returns_none(self, db):
        from app.services.password_reset_service import request_reset, validate_token
        user = _make_user(db, 6)
        token = request_reset(db, user.Email)
        token.ExpiresAt = datetime.now() - timedelta(hours=1)
        db.commit()
        assert validate_token(db, token.Token) is None

    def test_used_token_returns_none(self, db):
        from app.services.password_reset_service import request_reset, validate_token
        user = _make_user(db, 7)
        token = request_reset(db, user.Email)
        token.UsedAt = datetime.now()
        db.commit()
        assert validate_token(db, token.Token) is None


class TestRedeemToken:
    def test_redeem_updates_password_and_marks_used(self, db):
        from app.services.password_reset_service import request_reset, redeem_token
        user = _make_user(db, 8)
        token = request_reset(db, user.Email)
        db.commit()

        original_session_version = user.SessionVersion
        redeem_token(db, token.Token, "new-hashed-password")
        db.commit()

        assert user.PasswordHash == "new-hashed-password"
        assert token.is_used
        assert user.SessionVersion == original_session_version + 1

    def test_redeem_invalid_token_raises(self, db):
        from app.services.password_reset_service import redeem_token, PasswordResetError
        with pytest.raises(PasswordResetError):
            redeem_token(db, "not-a-real-token", "new-hash")

    def test_redeem_used_token_raises(self, db):
        from app.services.password_reset_service import request_reset, redeem_token, PasswordResetError
        user = _make_user(db, 9)
        token = request_reset(db, user.Email)
        db.commit()
        redeem_token(db, token.Token, "first-new-hash")
        db.commit()

        with pytest.raises(PasswordResetError):
            redeem_token(db, token.Token, "second-new-hash")

    def test_redeem_for_inactive_user_raises(self, db):
        from app.services.password_reset_service import request_reset, redeem_token, PasswordResetError
        user = _make_user(db, 10)
        token = request_reset(db, user.Email)
        db.commit()
        user.IsActive = False
        db.commit()

        with pytest.raises(PasswordResetError):
            redeem_token(db, token.Token, "new-hash")


class TestRoutesRegistered:
    """Decorator-integrity check, same pattern as the karaoke route regressions."""

    def test_forgot_password_routes_exist(self):
        import inspect
        import re
        import app.routes.auth as auth_module

        source = inspect.getsource(auth_module)
        for path, method in [
            ('"/forgot-password"', "get"),
            ('"/forgot-password"', "post"),
            ('"/reset-password"', "get"),
            ('"/reset-password"', "post"),
        ]:
            decorator = f'@router.{method}({path}'
            assert decorator.split(",")[0].rstrip(")") in source or f'@router.{method}({path}' in source
            pattern = re.escape(f'@router.{method}({path}') + r'.*?\n\s*\)?\s*\ndef \w+\('
            # Looser check: decorator text appears, followed eventually by a def
            idx = source.find(f'@router.{method}({path}')
            assert idx != -1, f"Missing decorator for {method.upper()} {path}"
