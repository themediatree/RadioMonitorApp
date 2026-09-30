"""Tests for subscriber_service (replaces test_tenant_service)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.database import Base


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
    from app.models.station import Station
    with Session(engine, expire_on_commit=False) as session:
        session.add(SubscriptionPlanConfig(PlanCode="standard", DisplayName="Standard", IsActive=True))
        session.add(SubscriptionPlanConfig(PlanCode="trial", DisplayName="Trial", IsActive=True))
        session.add(Station(StationID=10, StationName="Test FM", IsActive=True))
        session.commit()
    with Session(engine, expire_on_commit=False) as session:
        yield session


def test_create_subscriber_agent(db):
    from app.services.subscriber_service import create_subscriber
    s = create_subscriber(db, name="Oracle Sun", subscriber_type="Agent", plan="standard")
    db.commit()
    assert s.SubscriberID is not None
    assert s.Slug == "oracle-sun"
    assert s.SubscriberType == "Agent"


def test_create_subscriber_other_requires_description(db):
    from app.services.subscriber_service import create_subscriber, SubscriberError
    with pytest.raises(SubscriberError, match="description"):
        create_subscriber(db, name="X", subscriber_type="Other", plan="standard")


def test_create_subscriber_other_with_description(db):
    from app.services.subscriber_service import create_subscriber
    s = create_subscriber(db, name="Charity X", subscriber_type="Other",
                          plan="trial", other_description="Non-profit org")
    db.commit()
    assert s.OtherDescription == "Non-profit org"


def test_create_subscriber_radio_station_requires_station_id(db):
    from app.services.subscriber_service import create_subscriber, SubscriberError
    with pytest.raises(SubscriberError, match="station"):
        create_subscriber(db, name="Radio X", subscriber_type="Radio Station", plan="standard")


def test_create_subscriber_radio_station_with_station_id(db):
    from app.services.subscriber_service import create_subscriber
    s = create_subscriber(db, name="Test FM Subscriber", subscriber_type="Radio Station",
                          plan="standard", station_id=10)
    db.commit()
    assert s.StationID == 10


def test_create_subscriber_radio_station_duplicate_station(db):
    from app.services.subscriber_service import create_subscriber, SubscriberError
    with pytest.raises(SubscriberError, match="already has"):
        create_subscriber(db, name="Another FM", subscriber_type="Radio Station",
                          plan="standard", station_id=10)


def test_create_subscriber_invalid_plan(db):
    from app.services.subscriber_service import create_subscriber, SubscriberError
    with pytest.raises(SubscriberError, match="plan"):
        create_subscriber(db, name="Y", subscriber_type="Agent", plan="nonexistent")


def test_create_subscriber_invalid_type(db):
    from app.services.subscriber_service import create_subscriber, SubscriberError
    with pytest.raises(SubscriberError):
        create_subscriber(db, name="Z", subscriber_type="InvalidType", plan="standard")


def test_update_subscriber(db):
    from app.services.subscriber_service import create_subscriber, update_subscriber
    s = create_subscriber(db, name="To Update", subscriber_type="Advertiser", plan="trial")
    db.commit()
    updated = update_subscriber(db, s, name="Updated Name", plan="standard",
                                status="active", other_description=None,
                                contact_email="test@example.com", contact_phone=None)
    db.commit()
    assert updated.Name == "Updated Name"
    assert updated.ContactEmail == "test@example.com"


def test_slug_uniqueness(db):
    from app.services.subscriber_service import create_subscriber
    s1 = create_subscriber(db, name="Same Name Co", subscriber_type="Agent", plan="trial")
    s2 = create_subscriber(db, name="Same Name Co", subscriber_type="Agent", plan="trial")
    db.commit()
    assert s1.Slug != s2.Slug
    assert s2.Slug == "same-name-co-2"
