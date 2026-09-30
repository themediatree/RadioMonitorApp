"""
Tests for items 1+2+3:
  - Mobile number validation (SA format)
  - Subscriber deactivation service
  - Option-α re-staging on commercial withdrawal
"""

import os
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 1. Mobile number validation
# ---------------------------------------------------------------------------
class TestPhoneValidation:
    def test_valid_local_10_digit(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("0821234567") == "0821234567"

    def test_valid_with_spaces(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("082 123 4567") == "0821234567"

    def test_valid_with_hyphens(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("082-123-4567") == "0821234567"

    def test_valid_international_plus27(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("+27821234567") == "0821234567"

    def test_valid_international_0027(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("0027821234567") == "0821234567"

    def test_empty_returns_empty(self):
        from app.utils.email_validator import validate_phone
        assert validate_phone("") == ""
        assert validate_phone(None) == ""

    def test_invalid_too_short(self):
        from app.utils.email_validator import validate_phone, PhoneValidationError
        with pytest.raises(PhoneValidationError):
            validate_phone("082123")

    def test_invalid_landline_prefix(self):
        from app.utils.email_validator import validate_phone, PhoneValidationError
        with pytest.raises(PhoneValidationError):
            validate_phone("0111234567")  # 011 = Johannesburg landline

    def test_invalid_text(self):
        from app.utils.email_validator import validate_phone, PhoneValidationError
        with pytest.raises(PhoneValidationError):
            validate_phone("not-a-number")


# ---------------------------------------------------------------------------
# 2. Subscriber deactivation
# ---------------------------------------------------------------------------
class TestSubscriberDeactivation:
    @pytest.fixture(scope="class")
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker
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
        with Session(eng, expire_on_commit=False) as session:
            from app.models.subscription_plan import SubscriptionPlanConfig
            session.add(SubscriptionPlanConfig(PlanCode="std", DisplayName="Standard", IsActive=True))
            session.commit()
        with Session(eng, expire_on_commit=False) as session:
            yield session

    def test_archive_active_subscriber(self, db):
        from app.services.subscriber_service import create_subscriber, deactivate_subscriber
        sub = create_subscriber(db, name="Test Sub", subscriber_type="Agent", plan="std")
        sub.SubscriberStatus = "active"  # simulate invite acceptance
        db.commit()
        assert sub.SubscriberStatus == "active"
        deactivate_subscriber(db, sub, new_status="archived")
        db.commit()
        assert sub.SubscriberStatus == "archived"

    def test_cancel_active_subscriber(self, db):
        from app.services.subscriber_service import create_subscriber, deactivate_subscriber
        sub = create_subscriber(db, name="Cancel Sub", subscriber_type="Advertiser", plan="std")
        db.commit()
        deactivate_subscriber(db, sub, new_status="cancelled")
        db.commit()
        assert sub.SubscriberStatus == "cancelled"

    def test_deactivate_already_archived_raises(self, db):
        from app.services.subscriber_service import create_subscriber, deactivate_subscriber, SubscriberError
        sub = create_subscriber(db, name="Already Gone", subscriber_type="Agent", plan="std")
        db.commit()
        deactivate_subscriber(db, sub, new_status="archived")
        db.commit()
        with pytest.raises(SubscriberError, match="already"):
            deactivate_subscriber(db, sub, new_status="archived")

    def test_invalid_status_raises(self, db):
        from app.services.subscriber_service import create_subscriber, deactivate_subscriber, SubscriberError
        sub = create_subscriber(db, name="Bad Status", subscriber_type="Agent", plan="std")
        db.commit()
        with pytest.raises(SubscriberError):
            deactivate_subscriber(db, sub, new_status="deleted")


# ---------------------------------------------------------------------------
# 3. Option-α re-staging on withdrawal
# ---------------------------------------------------------------------------
class TestOptionAlphaWithdrawal:
    """
    Withdrawal of the file-owner row when active siblings exist triggers
    Option-α: the sibling's file is re-staged from the audio archive.
    """

    @pytest.fixture(scope="class")
    def engine(self):
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
    def db(self, engine):
        from sqlalchemy.orm import Session
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def _seed(self, db, tmp_path):
        """Seed two subscribers + commercials sharing a FingerprintID + an audio archive."""
        from datetime import datetime
        from app.models.campaign import Campaign, CampaignCommercial, CampaignStation, Commercial
        from app.models.station import Station
        from app.models.subscriber import Subscriber
        from app.models.subscription_plan import SubscriptionPlanConfig

        db.add(SubscriptionPlanConfig(PlanCode="std2", DisplayName="Std2", IsActive=True))
        db.add(Station(StationID=20, StationName="TestFM", IsActive=True))
        sub_a = Subscriber(Name="Alpha", Slug="alpha2", SubscriberType="Agent",
                           SubscriptionPlan="std2", SubscriberStatus="active")
        sub_b = Subscriber(Name="Beta", Slug="beta2", SubscriberType="Advertiser",
                           SubscriptionPlan="std2", SubscriberStatus="active")
        db.add_all([sub_a, sub_b]); db.flush()

        com_a = Commercial(CommercialName=f"{sub_a.SubscriberID}_TAPE1",
                           DisplayTapeID="TAPE1", SubscriberID=sub_a.SubscriberID,
                           CommercialType="generic", Status="active", IsActive=True,
                           FingerprintID="fp-alpha-test", CreatedAt=datetime(2026,1,1))
        com_b = Commercial(CommercialName=f"{sub_b.SubscriberID}_TAPE1",
                           DisplayTapeID="TAPE1", SubscriberID=sub_b.SubscriberID,
                           CommercialType="generic", Status="active", IsActive=True,
                           FingerprintID="fp-alpha-test", CreatedAt=datetime(2026,1,2))
        db.add_all([com_a, com_b]); db.flush()

        camp = Campaign(SubscriberID=sub_b.SubscriberID, Name="Beta Camp",
                        StartDate=datetime(2026,1,1).date(), IsActive=True)
        db.add(camp); db.flush()
        db.add(CampaignCommercial(CampaignID=camp.CampaignID, CommercialID=com_b.CommercialID))
        db.add(CampaignStation(CampaignID=camp.CampaignID, StationID=20))
        db.commit()

        # Create audio archive file
        archive_dir = tmp_path / "audio_archive"
        archive_dir.mkdir()
        arch_file = archive_dir / "fp-alpha-test.mp3"
        arch_file.write_bytes(b"FAKE_AUDIO")

        return com_a, com_b, str(archive_dir)

    def test_option_alpha_triggers_restage(self, db, tmp_path, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "audio_archive_root", str(tmp_path / "audio_archive"))

        staged = []
        import app.utils.fingerprint as fp_mod
        monkeypatch.setattr(fp_mod, "stage_from_archive",
                            lambda **kw: staged.append(kw["commercial_name"]) or [])

        com_a, com_b, arch = self._seed(db, tmp_path)

        from app.services.registration_service import withdraw_commercial
        result = withdraw_commercial(db, com_a)
        db.commit()

        assert com_a.Status == "withdrawn"
        assert com_a.IsActive is False
        assert "withdrawn" in com_a.DisplayTapeID
        assert result == com_b.CommercialName  # sibling's name returned
        assert com_b.CommercialName in staged   # sibling was restaged

    def test_no_siblings_no_restage(self, db, tmp_path, monkeypatch):
        from datetime import datetime
        from app.models.campaign import Commercial
        from app.models.subscriber import Subscriber
        from app.models.subscription_plan import SubscriptionPlanConfig
        from app.config import settings
        monkeypatch.setattr(settings, "audio_archive_root", str(tmp_path / "audio_archive"))

        db.add(SubscriptionPlanConfig(PlanCode="std3", DisplayName="Std3", IsActive=True))
        sub = Subscriber(Name="Solo", Slug="solo3", SubscriberType="Agent",
                         SubscriptionPlan="std3", SubscriberStatus="active")
        db.add(sub); db.flush()
        com = Commercial(CommercialName=f"{sub.SubscriberID}_SOLO",
                         DisplayTapeID="SOLO", SubscriberID=sub.SubscriberID,
                         CommercialType="generic", Status="active", IsActive=True,
                         FingerprintID="fp-solo-unique", CreatedAt=datetime(2026,1,1))
        db.add(com); db.flush(); db.commit()

        from app.services.registration_service import withdraw_commercial
        result = withdraw_commercial(db, com)
        db.commit()
        assert result is None  # no siblings, no re-staging
        assert com.Status == "withdrawn"
