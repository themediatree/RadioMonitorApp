"""Tests for billing B1 token service."""

import pytest
from decimal import Decimal


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


@pytest.fixture
def subscriber(db):
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.subscriber import Subscriber
    plan = db.get(SubscriptionPlanConfig, "tok_test")
    if not plan:
        db.add(SubscriptionPlanConfig(PlanCode="tok_test", DisplayName="Token Test", IsActive=True))
        db.flush()
    sub = Subscriber(Name="Token Tester", Slug=f"tok-tester-{id(db)}", SubscriberType="Agent",
                     SubscriptionPlan="tok_test", SubscriberStatus="active")
    db.add(sub); db.flush(); db.commit()
    return sub


class TestCalculateCost:
    def test_single_station_one_day(self):
        from app.services.token_service import calculate_cost
        from datetime import date
        cost = calculate_cost(1, date(2026, 6, 1), date(2026, 6, 1))
        assert cost == Decimal("24.0000")  # 1 station × 24 hours

    def test_five_stations_seven_days(self):
        from app.services.token_service import calculate_cost
        from datetime import date
        cost = calculate_cost(5, date(2026, 6, 1), date(2026, 6, 7))
        assert cost == Decimal("840.0000")  # 5 × 7 × 24

    def test_open_ended_charges_30_days(self):
        from app.services.token_service import calculate_cost
        from datetime import date
        cost = calculate_cost(2, date(2026, 6, 1), None)
        assert cost == Decimal("1440.0000")  # 2 × 30 × 24


class TestCreditDebit:
    def test_credit_increases_balance(self, db, subscriber):
        from app.services.token_service import credit, get_balance
        credit(db, subscriber.SubscriberID, Decimal("100"),
               description="Test credit")
        db.commit()
        assert get_balance(db, subscriber.SubscriberID) >= Decimal("100")

    def test_debit_decreases_balance(self, db, subscriber):
        from app.services.token_service import credit, debit, get_balance
        credit(db, subscriber.SubscriberID, Decimal("500"), description="Setup")
        db.commit()
        before = get_balance(db, subscriber.SubscriberID)
        debit(db, subscriber.SubscriberID, Decimal("100"), description="Test debit")
        db.commit()
        assert get_balance(db, subscriber.SubscriberID) == before - Decimal("100")

    def test_insufficient_balance_raises(self, db, subscriber):
        from app.services.token_service import get_or_create_account, debit, InsufficientTokensError
        account = get_or_create_account(db, subscriber.SubscriberID)
        account.TokenBalance = Decimal("5")
        db.flush()
        with pytest.raises(InsufficientTokensError):
            debit(db, subscriber.SubscriberID, Decimal("1000"), description="Should fail")

    def test_balance_after_is_correct_snapshot(self, db, subscriber):
        from app.services.token_service import credit, get_or_create_account
        account = get_or_create_account(db, subscriber.SubscriberID)
        account.TokenBalance = Decimal("200")
        db.flush()
        tx = credit(db, subscriber.SubscriberID, Decimal("50"), description="Snapshot test")
        assert tx.BalanceAfter == Decimal("250")


class TestTrialProvisioning:
    def test_trial_tokens_credited_on_plan_with_trial(self, db):
        from app.models.subscription_plan import SubscriptionPlanConfig
        from app.models.subscriber import Subscriber
        from app.services.token_service import provision_trial_tokens, get_balance
        db.add(SubscriptionPlanConfig(
            PlanCode="trial_t", DisplayName="Trial", IsActive=True,
            TrialTokens=Decimal("100")
        ))
        sub = Subscriber(Name="Trial User", Slug="trial-u", SubscriberType="Agent",
                         SubscriptionPlan="trial_t", SubscriberStatus="active")
        db.add(sub); db.flush(); db.commit()
        provision_trial_tokens(db, sub.SubscriberID, "trial_t")
        db.commit()
        assert get_balance(db, sub.SubscriberID) == Decimal("100")

    def test_no_double_credit(self, db):
        from app.models.subscription_plan import SubscriptionPlanConfig
        from app.models.subscriber import Subscriber
        from app.services.token_service import provision_trial_tokens, get_balance
        db.add(SubscriptionPlanConfig(
            PlanCode="trial_t2", DisplayName="Trial2", IsActive=True,
            TrialTokens=Decimal("50")
        ))
        sub = Subscriber(Name="Trial2", Slug="trial-u2", SubscriberType="Agent",
                         SubscriptionPlan="trial_t2", SubscriberStatus="active")
        db.add(sub); db.flush(); db.commit()
        provision_trial_tokens(db, sub.SubscriberID, "trial_t2")
        db.commit()
        provision_trial_tokens(db, sub.SubscriberID, "trial_t2")
        db.commit()
        assert get_balance(db, sub.SubscriberID) == Decimal("50")  # not 100
