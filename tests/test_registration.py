"""Registration service tests (v0.3 -- Subscriber-owned commercials)."""

import os, pytest, tempfile
from datetime import date, timedelta
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
def db_session(engine):
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.subscriber import Subscriber
    from app.models.station import Station
    with Session(engine, expire_on_commit=False) as session:
        session.add(SubscriptionPlanConfig(PlanCode="std", DisplayName="Standard", IsActive=True))
        session.add(Station(StationID=1, StationName="5FM", IsActive=True))
        sub = Subscriber(Name="Oracle Sun", Slug="oracle-sun", SubscriberType="Agent",
                         SubscriptionPlan="std", SubscriberStatus="active")
        session.add(sub)
        session.commit()
        session._sub_id = sub.SubscriberID
    with Session(engine, expire_on_commit=False) as session:
        session._sub_id = sub.SubscriberID
        yield session


def _future(days=1):
    return date.today() + timedelta(days=days)


class TestTapeIDValidation:
    def test_valid_tape_id(self):
        from app.services.registration_service import sanitize_tape_id
        assert sanitize_tape_id("NCHK_030_1735") == "NCHK_030_1735"

    def test_empty_tape_id_raises(self):
        from app.services.registration_service import sanitize_tape_id, RegistrationError
        with pytest.raises(RegistrationError):
            sanitize_tape_id("")

    def test_forbidden_chars_raise(self):
        from app.services.registration_service import sanitize_tape_id, RegistrationError
        with pytest.raises(RegistrationError):
            sanitize_tape_id("bad/path")


class TestCampaignCreateOrLocate:
    def test_creates_campaign(self, db_session):
        from app.services.registration_service import get_or_create_campaign
        camp, created = get_or_create_campaign(
            db_session, subscriber_id=db_session._sub_id,
            name="Q1 Campaign", start_date=_future(1), end_date=_future(30)
        )
        db_session.commit()
        assert created is True
        assert camp.SubscriberID == db_session._sub_id

    def test_locates_existing_campaign(self, db_session):
        from app.services.registration_service import get_or_create_campaign
        camp1, _ = get_or_create_campaign(
            db_session, subscriber_id=db_session._sub_id,
            name="Persistent Campaign", start_date=_future(1), end_date=None
        )
        db_session.commit()
        camp2, created = get_or_create_campaign(
            db_session, subscriber_id=db_session._sub_id,
            name="Persistent Campaign", start_date=_future(5), end_date=None
        )
        db_session.commit()
        assert created is False
        assert camp1.CampaignID == camp2.CampaignID


class TestStagingFanOut:
    def test_fan_out_writes_per_station(self, tmp_path, monkeypatch):
        import app.services.registration_service as reg_svc
        from app.services.registration_service import fan_out_to_staging

        monkeypatch.setattr(
            reg_svc, "_staging_path",
            lambda station_name, category, tape_id, ext:
                str(tmp_path / station_name / category / f"{tape_id}{ext}")
        )
        src = tmp_path / "source.mp3"
        src.write_bytes(b"FAKE_MP3")
        fan_out_to_staging(
            station_names=["5FM", "HOT1027"],
            category="generic",
            tape_id="42_NCHK_030",
            source_path=str(src),
            ext=".mp3",
        )
        assert (tmp_path / "5FM" / "generic" / "42_NCHK_030.mp3").exists()
        assert (tmp_path / "HOT1027" / "generic" / "42_NCHK_030.mp3").exists()

    def test_fan_out_uses_write_then_rename(self, tmp_path, monkeypatch):
        import app.services.registration_service as reg_svc
        from app.services.registration_service import fan_out_to_staging

        monkeypatch.setattr(
            reg_svc, "_staging_path",
            lambda station_name, category, tape_id, ext:
                str(tmp_path / station_name / category / f"{tape_id}{ext}")
        )
        src = tmp_path / "source2.mp3"
        src.write_bytes(b"FAKE")
        fan_out_to_staging(
            station_names=["5FM"],
            category="generic",
            tape_id="42_CLEAN",
            source_path=str(src),
            ext=".mp3",
        )
        assert not (tmp_path / "5FM" / "generic" / "42_CLEAN.mp3.tmp").exists()
        assert (tmp_path / "5FM" / "generic" / "42_CLEAN.mp3").exists()


class TestActivation:
    def test_activate_due_commercials(self, db_session):
        from app.models.campaign import Campaign, CampaignCommercial, Commercial
        from app.services.registration_service import activate_due_commercials

        sub_id = db_session._sub_id
        camp = Campaign(SubscriberID=sub_id, Name="Activation Test",
                        StartDate=_future(-1), IsActive=True)
        db_session.add(camp)
        db_session.flush()

        commercial = Commercial(
            CommercialName=f"{sub_id}_ACT001", DisplayTapeID="ACT001",
            SubscriberID=sub_id, CommercialType="generic",
            Status="pending", IsActive=True,
        )
        db_session.add(commercial)
        db_session.flush()
        db_session.add(CampaignCommercial(
            CampaignID=camp.CampaignID, CommercialID=commercial.CommercialID
        ))
        db_session.commit()

        activated = activate_due_commercials(db_session)
        db_session.commit()
        assert commercial.CommercialID in activated
        assert commercial.Status == "active"
