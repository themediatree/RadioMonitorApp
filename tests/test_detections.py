"""
Detection-viewing tests (v0.3 -- Subscriber + FingerprintID scoping).

Three concerns:
  - list/detail scoping via FingerprintID join
  - get_visible_detection returns None for missing AND out-of-scope (no leak)
  - clip path resolution refuses traversal, absolute-outside-root, and missing
"""

from datetime import datetime
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
def world(engine):
    """
    Sub A and Sub B share the same audio (same FingerprintID).
    Sub C has different audio. One detection fires (keyed to Sub A's commercial).
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

        sub_a = Subscriber(Name="Sub A", Slug="det-sub-a", SubscriberType="Agent",
                           SubscriptionPlan="std", SubscriberStatus="active")
        sub_b = Subscriber(Name="Sub B", Slug="det-sub-b", SubscriberType="Advertiser",
                           SubscriptionPlan="std", SubscriberStatus="active")
        sub_c = Subscriber(Name="Sub C", Slug="det-sub-c", SubscriberType="Government",
                           SubscriptionPlan="std", SubscriberStatus="active")
        db.add_all([sub_a, sub_b, sub_c]); db.flush()

        com_a = Commercial(CommercialName="1_CLOVER", DisplayTapeID="CLOVER",
                           SubscriberID=sub_a.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-clover",
                           CreatedAt=datetime(2026, 1, 1))
        com_b = Commercial(CommercialName="2_CLOVER", DisplayTapeID="CLOVER",
                           SubscriberID=sub_b.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-clover",
                           CreatedAt=datetime(2026, 1, 1))
        com_c = Commercial(CommercialName="3_OTHER", DisplayTapeID="OTHER",
                           SubscriberID=sub_c.SubscriberID, CommercialType="generic",
                           Status="active", IsActive=True, FingerprintID="fp-other",
                           CreatedAt=datetime(2026, 1, 1))
        db.add_all([com_a, com_b, com_c]); db.flush()

        det = Detection(DetectionID=100, ChunkID=1, CommercialID=com_a.CommercialID,
                        StationID=1, Category="commercial", StartTimeSec=0, EndTimeSec=30,
                        ClipPath="5FM/2026-04-01/clip1.mp3",
                        CreatedAt=datetime(2026, 4, 1, 12, 0))
        db.add(det); db.flush()

        user_int = User(Email="di@rm.com", PasswordHash="x", UserType="internal", IsActive=True)
        user_a = User(Email="da@sub.com", PasswordHash="x", UserType="subscriber_user",
                      SubscriberID=sub_a.SubscriberID, IsActive=True)
        user_b = User(Email="db@sub.com", PasswordHash="x", UserType="subscriber_user",
                      SubscriberID=sub_b.SubscriberID, IsActive=True)
        user_c = User(Email="dc@sub.com", PasswordHash="x", UserType="subscriber_user",
                      SubscriberID=sub_c.SubscriberID, IsActive=True)
        db.add_all([user_int, user_a, user_b, user_c]); db.flush()

        ids["det_id"] = det.DetectionID
        ids["user_int"] = user_int.UserID
        ids["user_a"] = user_a.UserID
        ids["user_b"] = user_b.UserID
        ids["user_c"] = user_c.UserID
        db.commit()
    return ids


def _listed_ids(engine, user_id):
    from app.models.user import User
    from app.services.detection_service import list_commercial_detections
    with Session(engine, expire_on_commit=False) as db:
        user = db.get(User, user_id)
        rows, total = list_commercial_detections(db, user)
    return {r["detection"].DetectionID for r in rows}, total


class TestListScoping:
    def test_internal_sees_detection(self, engine, world):
        ids, total = _listed_ids(engine, world["user_int"])
        assert world["det_id"] in ids

    def test_sub_a_sees_detection(self, engine, world):
        ids, _ = _listed_ids(engine, world["user_a"])
        assert world["det_id"] in ids

    def test_sub_b_sees_detection_via_fingerprint(self, engine, world):
        ids, _ = _listed_ids(engine, world["user_b"])
        assert world["det_id"] in ids

    def test_sub_c_sees_nothing(self, engine, world):
        ids, total = _listed_ids(engine, world["user_c"])
        assert ids == set()
        assert total == 0


class TestDetailNonLeak:
    def test_missing_returns_none(self, engine, world):
        from app.models.user import User
        from app.services.detection_service import get_visible_detection
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_int"])
            assert get_visible_detection(db, user, 9999) is None

    def test_out_of_scope_returns_none(self, engine, world):
        from app.models.user import User
        from app.services.detection_service import get_visible_detection
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_c"])
            assert get_visible_detection(db, user, world["det_id"]) is None

    def test_in_scope_returns_row(self, engine, world):
        from app.models.user import User
        from app.services.detection_service import get_visible_detection
        with Session(engine, expire_on_commit=False) as db:
            user = db.get(User, world["user_a"])
            item = get_visible_detection(db, user, world["det_id"])
            assert item is not None
            assert item["detection"].DetectionID == world["det_id"]


class TestClipPathResolution:
    def setup_method(self, method):
        import os, tempfile
        from app.config import settings
        self._tmp = tempfile.mkdtemp(prefix="cliproot_")
        station_dir = os.path.join(self._tmp, "5FM", "2026-04-01")
        os.makedirs(station_dir, exist_ok=True)
        self._real_clip = os.path.join(station_dir, "clip1.mp3")
        with open(self._real_clip, "wb") as f:
            f.write(b"FAKE")
        self._orig_root = settings.clips_path
        settings.clips_path = self._tmp

    def teardown_method(self, method):
        import shutil
        from app.config import settings
        settings.clips_path = self._orig_root
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_resolves_relative_path(self):
        import os
        from app.services.clip_service import resolve_clip_path
        result = resolve_clip_path("5FM/2026-04-01/clip1.mp3")
        assert result is not None
        assert os.path.samefile(result, self._real_clip)

    def test_rejects_missing_file(self):
        from app.services.clip_service import resolve_clip_path
        assert resolve_clip_path("5FM/2026-04-01/nope.mp3") is None

    def test_rejects_empty_path(self):
        from app.services.clip_service import resolve_clip_path
        assert resolve_clip_path("") is None
        assert resolve_clip_path(None) is None

    def test_rejects_traversal(self):
        from app.services.clip_service import resolve_clip_path
        assert resolve_clip_path("../../etc/passwd") is None
        assert resolve_clip_path("5FM/../../../etc/passwd") is None

    def test_rejects_absolute_outside_root(self, tmp_path):
        outside = tmp_path / "rogue.mp3"
        outside.write_bytes(b"x")
        from app.services.clip_service import resolve_clip_path
        assert resolve_clip_path(str(outside)) is None


class TestParseAired:
    def _chunk(self, chunk_date=None, start_time=None, filename=None):
        from unittest.mock import MagicMock
        c = MagicMock()
        c.ChunkDate = chunk_date
        c.StartTime = start_time
        c.FileName = filename
        return c

    def test_parses_from_db_columns(self):
        from app.services.detection_service import parse_aired
        # StartTimeSec=31543 = 08:45:43 from midnight; EndTimeSec=31573 = 08:46:13
        chunk = self._chunk(chunk_date="2026-06-05", start_time="08:45:02")
        r = parse_aired(chunk, 31543, 31573)
        assert r["aired_date"] == "2026-06-05"
        assert r["start_clock"] == "08:45:43"
        assert r["end_clock"] == "08:46:13"

    def test_fallback_to_filename(self):
        from app.services.detection_service import parse_aired
        chunk = self._chunk(filename="5FM_26-06-05_080105.mp3")
        r = parse_aired(chunk, 0, 30)
        assert r["aired_date"] == "2026-06-05"

    def test_handles_none_chunk(self):
        from app.services.detection_service import parse_aired
        r = parse_aired(None, 0, 30)
        assert r["aired_date"] is None
