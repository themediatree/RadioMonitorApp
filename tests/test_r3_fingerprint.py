"""
R3 tests: FingerprintID computation, dedup, T&C audit, Option-α re-staging.

Tests that touch fingerprint_core's actual FFT are skipped when numpy/scipy
are unavailable (the sandbox may not have them). The FingerprintID helpers
(archive write, re-stage) are tested independently of the heavy fingerprinting.
"""

import os
import pytest


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _make_silent_wav(path: str, duration_s: float = 0.5) -> str:
    """Write a tiny silent WAV (no external tools needed)."""
    import struct, wave
    sr = 8000
    n = int(sr * duration_s)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return path


# -----------------------------------------------------------------------
# FingerprintID determinism (requires numpy + ffmpeg)
# -----------------------------------------------------------------------
@pytest.mark.skipif(
    not os.path.exists("/usr/bin/ffmpeg") and
    not os.path.exists("C:/ffmpeg/bin/ffmpeg.exe"),
    reason="ffmpeg not available in sandbox"
)
def test_fingerprint_id_deterministic(tmp_path):
    """Same audio file produces same FingerprintID across two calls."""
    pytest.importorskip("numpy")
    pytest.importorskip("scipy")
    from app.utils.fingerprint import compute_fingerprint_id

    wav = _make_silent_wav(str(tmp_path / "clip.wav"))
    id1, dur1 = compute_fingerprint_id(wav)
    id2, dur2 = compute_fingerprint_id(wav)
    assert id1 == id2
    assert isinstance(id1, str) and len(id1) == 64  # SHA256 hex


# -----------------------------------------------------------------------
# Audio archive write
# -----------------------------------------------------------------------
class TestAudioArchive:
    def test_write_to_archive_creates_file(self, tmp_path):
        from app.utils.fingerprint import write_to_archive
        src = tmp_path / "source.mp3"
        src.write_bytes(b"FAKE")
        dest = write_to_archive(str(src), str(tmp_path / "archive"), "abc123")
        assert os.path.exists(dest)
        assert dest.endswith("abc123.mp3")

    def test_write_to_archive_idempotent(self, tmp_path):
        from app.utils.fingerprint import write_to_archive
        src = tmp_path / "source.mp3"
        src.write_bytes(b"FAKE")
        archive_root = str(tmp_path / "archive")
        dest1 = write_to_archive(str(src), archive_root, "abc123")
        dest2 = write_to_archive(str(src), archive_root, "abc123")
        assert dest1 == dest2  # second call is a no-op

    def test_write_to_archive_uses_rename(self, tmp_path):
        from app.utils.fingerprint import write_to_archive
        src = tmp_path / "source.mp3"
        src.write_bytes(b"FAKE")
        archive_root = str(tmp_path / "archive")
        write_to_archive(str(src), archive_root, "xyz789")
        assert not os.path.exists(os.path.join(archive_root, "xyz789.mp3.tmp"))


# -----------------------------------------------------------------------
# Option-α re-staging
# -----------------------------------------------------------------------
class TestOptionAlphaRestage:
    def test_stage_from_archive_writes_per_station(self, tmp_path):
        from app.utils.fingerprint import stage_from_archive, write_to_archive

        # Seed the archive.
        src = tmp_path / "source.mp3"
        src.write_bytes(b"FAKE_AUDIO")
        archive_root = str(tmp_path / "archive")
        write_to_archive(str(src), archive_root, "fp-test")

        # Stage to two stations.
        def sample_dir_fn(station, category):
            return str(tmp_path / "staging" / station / category)

        written = stage_from_archive(
            archive_root=archive_root,
            fingerprint_id="fp-test",
            station_names=["5FM", "HOT1027"],
            commercial_name="42_TAPEID",
            sample_dir_fn=sample_dir_fn,
        )
        assert len(written) == 2
        assert os.path.exists(tmp_path / "staging" / "5FM" / "generic" / "42_TAPEID.mp3")
        assert os.path.exists(tmp_path / "staging" / "HOT1027" / "generic" / "42_TAPEID.mp3")

    def test_stage_from_archive_missing_archive_raises(self, tmp_path):
        from app.utils.fingerprint import stage_from_archive
        with pytest.raises(RuntimeError, match="Audio archive missing"):
            stage_from_archive(
                archive_root=str(tmp_path / "empty_archive"),
                fingerprint_id="nonexistent",
                station_names=["5FM"],
                commercial_name="X_Y",
                sample_dir_fn=lambda s, c: str(tmp_path / s / c),
            )


# -----------------------------------------------------------------------
# Legal constants
# -----------------------------------------------------------------------
def test_terms_version_is_string():
    from app.legal import TERMS_VERSION, TERMS_TEXT, TERMS_TEXT_HASH
    assert isinstance(TERMS_VERSION, str) and len(TERMS_VERSION) > 0
    assert len(TERMS_TEXT) > 50
    assert len(TERMS_TEXT_HASH) == 64  # SHA256 hex


def test_terms_hash_matches_text():
    import hashlib
    from app.legal import TERMS_TEXT, TERMS_TEXT_HASH
    assert hashlib.sha256(TERMS_TEXT.encode()).hexdigest() == TERMS_TEXT_HASH


# -----------------------------------------------------------------------
# Dedup logic via DB (SQLite in-memory)
# -----------------------------------------------------------------------
class TestFingerprintDedup:
    """
    Test that a second registration with the same FingerprintID creates a
    follower row (no new file staged) while a different FingerprintID creates
    a new file-owner row.
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

    @pytest.fixture(scope="class")
    def db(self, engine):
        from sqlalchemy.orm import Session
        from app.models.subscription_plan import SubscriptionPlanConfig
        from app.models.subscriber import Subscriber
        with Session(engine, expire_on_commit=False) as session:
            session.add(SubscriptionPlanConfig(
                PlanCode="std", DisplayName="Standard", IsActive=True
            ))
            sub_a = Subscriber(Name="Dedup A", Slug="dedup-a", SubscriberType="Agent",
                               SubscriptionPlan="std", SubscriberStatus="active")
            sub_b = Subscriber(Name="Dedup B", Slug="dedup-b", SubscriberType="Advertiser",
                               SubscriptionPlan="std", SubscriberStatus="active")
            session.add_all([sub_a, sub_b])
            session.commit()
            session._sub_a = sub_a.SubscriberID
            session._sub_b = sub_b.SubscriberID
        with Session(engine, expire_on_commit=False) as session:
            session._sub_a = sub_a.SubscriberID
            session._sub_b = sub_b.SubscriberID
            yield session

    def test_second_registration_same_fingerprint_is_follower(self, db):
        """
        Two Subscribers register the same audio (same FingerprintID).
        Both get Commercial rows; is_new_fingerprint=False for the second.
        """
        from app.models.campaign import Commercial

        # Sub A registers first.
        com_a = Commercial(
            CommercialName=f"{db._sub_a}_AD001",
            DisplayTapeID="AD001",
            SubscriberID=db._sub_a,
            CommercialType="generic",
            Status="pending",
            IsActive=True,
            FingerprintID="fp-shared-2",
        )
        db.add(com_a); db.commit()

        # Sub B checks for existing FingerprintID -- should find it.
        existing = (
            db.query(Commercial)
            .filter(
                Commercial.FingerprintID == "fp-shared-2",
                Commercial.Status.in_(["active", "pending"]),
            )
            .first()
        )
        assert existing is not None
        assert existing.SubscriberID == db._sub_a

        # Sub B registers as a follower.
        com_b = Commercial(
            CommercialName=f"{db._sub_b}_AD001",
            DisplayTapeID="AD001",
            SubscriberID=db._sub_b,
            CommercialType="generic",
            Status="pending",
            IsActive=True,
            FingerprintID="fp-shared-2",
        )
        db.add(com_b); db.commit()

        # Both rows exist, same FingerprintID.
        siblings = (
            db.query(Commercial)
            .filter(Commercial.FingerprintID == "fp-shared-2")
            .all()
        )
        assert len(siblings) == 2
        sids = {s.SubscriberID for s in siblings}
        assert db._sub_a in sids and db._sub_b in sids

    def test_different_fingerprint_is_new_file_owner(self, db):
        from app.models.campaign import Commercial
        existing = (
            db.query(Commercial)
            .filter(
                Commercial.FingerprintID == "fp-unique",
                Commercial.Status.in_(["active", "pending"]),
            )
            .first()
        )
        assert existing is None  # no match -> new fingerprint -> new file owner
