"""
Scoping tests (v0.3 -- FingerprintID-based commercial visibility).

Key properties:
  - internal sees everything
  - subscriber user sees detections whose Commercial shares a FingerprintID
    with one of their own active Commercials
  - unrelated Subscriber sees nothing (isolation guarantee)
  - out-of-scope Subscriber does not see another's detections

Tests run against SQLite in-memory. The FingerprintID join uses aliased()
queries; dialect safety is verified separately.
"""

import pytest
from datetime import datetime
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
def world(engine):
    """
    Two Subscribers (A and B) both register the same audio (same FingerprintID).
    Subscriber C registers different audio (different FingerprintID).
    One Detection fires for the shared fingerprint.
    """
    from app.models.campaign import Commercial
    from app.models.detection import Detection
    from app.models.station import Station
    from app.models.subscriber import Subscriber
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.user import User

    ids = {}
    with Session(engine, expire_on_commit=False) as db:
        db.add(SubscriptionPlanConfig(PlanCode="std", DisplayName="Standard", IsActive=True))
        db.add(Station(StationID=1, StationName="5FM", IsActive=True))

        sub_a = Subscriber(Name="Sub A", Slug="sub-a", SubscriberType="Agent",
                           SubscriptionPlan="std", SubscriberStatus="active")
        sub_b = Subscriber(Name="Sub B", Slug="sub-b", SubscriberType="Advertiser",
                           SubscriptionPlan="std", SubscriberStatus="active")
        sub_c = Subscriber(Name="Sub C", Slug="sub-c", SubscriberType="Government",
                           SubscriptionPlan="std", SubscriberStatus="active")
        db.add_all([sub_a, sub_b, sub_c]); db.flush()

        # A and B share FingerprintID "fp-shared"
        com_a = Commercial(CommercialName="1_TAPEAID", DisplayTapeID="TAPEAID",
                           SubscriberID=sub_a.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-shared",
                           CreatedAt=datetime(2026, 1, 1))
        com_b = Commercial(CommercialName="2_TAPEAID", DisplayTapeID="TAPEAID",
                           SubscriberID=sub_b.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-shared",
                           CreatedAt=datetime(2026, 1, 1))
        # C has different fingerprint
        com_c = Commercial(CommercialName="3_OTHER", DisplayTapeID="OTHER",
                           SubscriberID=sub_c.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-c",
                           CreatedAt=datetime(2026, 1, 1))
        db.add_all([com_a, com_b, com_c]); db.flush()

        det = Detection(DetectionID=1, ChunkID=1,
                        CommercialID=com_a.CommercialID, StationID=1,
                        Category="commercial", StartTimeSec=0, EndTimeSec=30,
                        ClipPath="5FM/2026/clip.mp3",
                        CreatedAt=datetime(2026, 4, 1, 12, 0))
        db.add(det); db.flush()

        user_internal = User(Email="i@rm.com", PasswordHash="x",
                             UserType="internal", IsActive=True)
        user_a = User(Email="a@sub.com", PasswordHash="x",
                      UserType="subscriber_user",
                      SubscriberID=sub_a.SubscriberID, IsActive=True)
        user_b = User(Email="b@sub.com", PasswordHash="x",
                      UserType="subscriber_user",
                      SubscriberID=sub_b.SubscriberID, IsActive=True)
        user_c = User(Email="c@sub.com", PasswordHash="x",
                      UserType="subscriber_user",
                      SubscriberID=sub_c.SubscriberID, IsActive=True)
        db.add_all([user_internal, user_a, user_b, user_c]); db.flush()

        ids["det_id"] = det.DetectionID
        ids["user_internal"] = user_internal.UserID
        ids["user_a"] = user_a.UserID
        ids["user_b"] = user_b.UserID
        ids["user_c"] = user_c.UserID
        db.commit()
    return ids


def _visible_ids(engine, user_id):
    from app.models.detection import Detection
    from app.models.user import User
    from app.services.scoping import commercial_filter
    with Session(engine, expire_on_commit=False) as db:
        user = db.get(User, user_id)
        rows = db.query(Detection).filter(commercial_filter(user)).all()
        return {r.DetectionID for r in rows}


class TestCommercialScoping:
    def test_internal_sees_detection(self, engine, world):
        assert world["det_id"] in _visible_ids(engine, world["user_internal"])

    def test_sub_a_sees_detection_via_fingerprint(self, engine, world):
        assert world["det_id"] in _visible_ids(engine, world["user_a"])

    def test_sub_b_sees_detection_via_fingerprint(self, engine, world):
        assert world["det_id"] in _visible_ids(engine, world["user_b"])

    def test_sub_c_does_not_see_detection(self, engine, world):
        assert world["det_id"] not in _visible_ids(engine, world["user_c"])


class TestWithdrawnCommercialScoping:
    def test_withdrawn_commercial_excluded_from_scope(self, engine, world):
        from app.models.campaign import Commercial
        from app.models.user import User
        from app.services.scoping import commercial_filter
        from app.models.detection import Detection

        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_b"])
            # Withdraw B's commercial.
            com_b = (db.query(Commercial)
                     .filter(Commercial.SubscriberID == user.SubscriberID)
                     .first())
            com_b.Status = "withdrawn"
            db.commit()

        # B should no longer see the detection (their active row is gone).
        assert world["det_id"] not in _visible_ids(engine, world["user_b"])

        # A should still see it (A's row is still active).
        assert world["det_id"] in _visible_ids(engine, world["user_a"])

        # Restore for other tests.
        with Session(engine, expire_on_commit=False) as db:
            com_b = (db.query(Commercial)
                     .filter(Commercial.SubscriberID == world["user_b"])
                     .first())
            if com_b:
                com_b.Status = "active"
                db.commit()


class TestScopingDialectSafety:
    """The commercial scoping query must compile to valid T-SQL."""

    def test_commercial_filter_compiles_to_tsql(self, engine, world):
        from sqlalchemy.dialects import mssql
        from app.models.detection import Detection
        from app.models.user import User
        from app.services.scoping import commercial_filter

        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_a"])
            filt = commercial_filter(user)
            stmt = db.query(Detection).filter(filt).statement
            compiled = stmt.compile(dialect=mssql.dialect())
            sql = str(compiled)
            # FingerprintID join must be present; boolean IS 1 must NOT appear
            assert "FingerprintID" in sql
            assert "IS 1" not in sql and "IS TRUE" not in sql


class TestLivereadScoping:
    """Liveread commercials have NULL FingerprintID -- direct ownership check."""

    def test_subscriber_sees_own_liveread_detection(self, engine, world):
        from app.models.campaign import Commercial
        from app.models.detection import Detection
        from app.models.user import User
        from app.services.scoping import commercial_filter

        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_a"])
            # Create a liveread commercial owned by Sub A (no FingerprintID).
            lr = Commercial(
                CommercialName=f"{user.SubscriberID}_LIVEREAD01",
                DisplayTapeID="LIVEREAD01",
                SubscriberID=user.SubscriberID,
                CommercialType="liveread",
                Status="active", IsActive=True,
                FingerprintID=None,
                CreatedAt=__import__("datetime").datetime(2026, 1, 1),
            )
            db.add(lr); db.flush()
            det = Detection(
                DetectionID=999, ChunkID=99, CommercialID=lr.CommercialID, StationID=1,
                Category="liveread", StartTimeSec=0, EndTimeSec=30,
                ClipPath="5FM/lr/clip.mp3",
                CreatedAt=__import__("datetime").datetime(2026, 4, 2, 10, 0),
            )
            db.add(det); db.flush()
            lr_det_id = det.DetectionID
            db.commit()

        # Sub A should see it.
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_a"])
            rows = db.query(Detection).filter(commercial_filter(user)).all()
            assert any(r.DetectionID == lr_det_id for r in rows)

        # Sub C (unrelated) should NOT see it.
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_c"])
            rows = db.query(Detection).filter(commercial_filter(user)).all()
            assert not any(r.DetectionID == lr_det_id for r in rows)
    """Song/word scoping is internal-only until those UIs are built."""

    def test_song_filter_internal_sees_all(self, engine, world):
        from app.models.user import User
        from app.services.scoping import song_filter
        from sqlalchemy.sql import true
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_internal"])
            assert song_filter(user) is not None

    def test_song_filter_subscriber_sees_nothing(self, engine, world):
        from app.models.user import User
        from app.services.scoping import song_filter
        from sqlalchemy.sql.elements import False_
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_a"])
            result = song_filter(user)
            assert isinstance(result, False_)
