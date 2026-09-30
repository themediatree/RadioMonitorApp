"""Subscriber management smoke tests (replaces test_tenant_management)."""

import pytest
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
def seeded_db(engine):
    from app.models.subscription_plan import SubscriptionPlanConfig
    with Session(engine, expire_on_commit=False) as db:
        db.add(SubscriptionPlanConfig(PlanCode="std", DisplayName="Standard", IsActive=True))
        db.commit()
    with Session(engine, expire_on_commit=False) as db:
        yield db


def test_create_and_list_subscribers(seeded_db):
    from app.services.subscriber_service import create_subscriber
    from app.models.subscriber import Subscriber
    create_subscriber(seeded_db, name="Pick & Pay", subscriber_type="Advertiser", plan="std")
    create_subscriber(seeded_db, name="ANC", subscriber_type="Political Party", plan="std")
    seeded_db.commit()
    all_subs = seeded_db.query(Subscriber).all()
    names = {s.Name for s in all_subs}
    assert "Pick & Pay" in names
    assert "ANC" in names


def test_subscriber_type_values_accepted(seeded_db):
    from app.services.subscriber_service import create_subscriber
    types_and_kwargs = [
        ("Agent", {}),
        ("Advertiser", {}),
        ("Brand Owner", {}),
        ("Government", {}),
        ("Political Party", {}),
        ("Other", {"other_description": "Test org"}),
    ]
    for sub_type, extra in types_and_kwargs:
        s = create_subscriber(seeded_db, name=f"Test {sub_type}",
                              subscriber_type=sub_type, plan="std", **extra)
        seeded_db.commit()
        assert s.SubscriberType == sub_type
