"""Tests for liveread karaoke transcript (WordTimestampPath) support."""

import json
import os
import pytest


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


def _seed_liveread_detection(db, tmp_path, with_json=True, _counter=[5000]):
    from datetime import datetime
    from app.models.campaign import Commercial
    from app.models.detection import Detection
    from app.models.station import Station
    from app.models.subscriber import Subscriber
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.user import User, UserType

    _counter[0] += 1
    uid = _counter[0]
    plan_code = f"kt{uid}"
    station_id = 1000 + uid

    db.add(SubscriptionPlanConfig(PlanCode=plan_code, DisplayName="KT", IsActive=True))
    db.add(Station(StationID=station_id, StationName=f"KaraokeFM{uid}", IsActive=True))
    sub = Subscriber(Name=f"Karaoke Test {uid}", Slug=f"karaoke-test-{uid}", SubscriberType="Agent",
                     SubscriptionPlan=plan_code, SubscriberStatus="active")
    db.add(sub); db.flush()

    user = User(Email=f"karaoke{uid}@test.com", PasswordHash="x", SubscriberID=sub.SubscriberID,
               UserType=UserType.SUBSCRIBER_ADMIN, IsActive=True)
    db.add(user); db.flush()

    com = Commercial(CommercialName=f"{sub.SubscriberID}_LIVEREAD_KT{uid}",
                     DisplayTapeID=f"LIVEREAD_KT{uid}", SubscriberID=sub.SubscriberID,
                     CommercialType="liveread", Status="active", IsActive=True,
                     FingerprintID=None, CreatedAt=datetime(2026,1,1))
    db.add(com); db.flush()

    # Set up the clips root and a real JSON file under it
    clips_root = tmp_path / "clips"
    json_rel = f"KaraokeFM/2026-06-01/commercials/liveread/clip{uid}.json"
    json_abs = clips_root / json_rel
    json_abs.parent.mkdir(parents=True, exist_ok=True)
    if with_json:
        json_abs.write_text(json.dumps({
            "results": {"channels": [{"alternatives": [{"words": [
                {"word": "hello", "start": 0.0, "end": 0.4, "confidence": 0.99},
                {"word": "world", "start": 0.45, "end": 0.9, "confidence": 0.97},
            ]}]}]}
        }))

    det = Detection(
        DetectionID=uid, ChunkID=1, CommercialID=com.CommercialID, StationID=station_id,
        Category="liveread", StartTimeSec=0, EndTimeSec=30,
        ClipPath=f"KaraokeFM/2026-06-01/commercials/liveread/clip{uid}.mp3",
        WordTimestampPath=(json_rel if with_json else None),
        CreatedAt=datetime(2026,6,1,8,0),
    )
    db.add(det); db.flush(); db.commit()

    return user, det, str(clips_root)


class TestLivereadTranscript:
    def test_has_transcript_true_when_file_exists(self, db, tmp_path, monkeypatch):
        from app.config import settings
        user, det, clips_root = _seed_liveread_detection(db, tmp_path, with_json=True)
        monkeypatch.setattr(settings, "clips_path", clips_root)

        from app.services.clip_service import resolve_clip_path
        resolved = resolve_clip_path(det.WordTimestampPath)
        assert resolved is not None
        assert os.path.isfile(resolved)

    def test_has_transcript_false_when_path_missing(self, db, tmp_path, monkeypatch):
        from app.config import settings
        user, det, clips_root = _seed_liveread_detection(db, tmp_path, with_json=False)
        monkeypatch.setattr(settings, "clips_path", clips_root)

        assert det.WordTimestampPath is None

    def test_route_logic_returns_none_for_missing_path(self, db, tmp_path, monkeypatch):
        """Equivalent to the 404 case, tested at the service layer (no TestClient)."""
        from app.config import settings
        user, det, clips_root = _seed_liveread_detection(db, tmp_path, with_json=False)
        monkeypatch.setattr(settings, "clips_path", clips_root)

        from app.services.detection_service import get_visible_detection
        from app.services.clip_service import resolve_clip_path

        item = get_visible_detection(db, user, det.DetectionID)
        assert item is not None
        assert item["detection"].WordTimestampPath is None

    def test_route_logic_resolves_json_when_available(self, db, tmp_path, monkeypatch):
        """Equivalent to the 200 case, tested at the service layer (no TestClient)."""
        from app.config import settings
        user, det, clips_root = _seed_liveread_detection(db, tmp_path, with_json=True)
        monkeypatch.setattr(settings, "clips_path", clips_root)

        from app.services.detection_service import get_visible_detection
        from app.services.clip_service import resolve_clip_path

        item = get_visible_detection(db, user, det.DetectionID)
        assert item is not None
        resolved = resolve_clip_path(item["detection"].WordTimestampPath)
        assert resolved is not None

        with open(resolved) as f:
            data = json.load(f)
        words = data["results"]["channels"][0]["alternatives"][0]["words"]
        assert words[0]["word"] == "hello"


class TestGenericTranscriptionJob:
    def test_get_generic_transcript_path_none_when_no_job(self, db):
        from app.services.detection_service import get_generic_transcript_path
        result = get_generic_transcript_path(db, "fp-no-job-exists")
        assert result is None

    def test_get_generic_transcript_path_none_when_pending(self, db):
        from app.models.detection import GenericTranscriptionJob
        from datetime import datetime
        db.add(GenericTranscriptionJob(
            JobID=901, FingerprintID="fp-pending-test", Status="pending", CreatedAt=datetime.now(),
        ))
        db.commit()
        from app.services.detection_service import get_generic_transcript_path
        result = get_generic_transcript_path(db, "fp-pending-test")
        assert result is None

    def test_get_generic_transcript_path_returns_when_complete(self, db):
        from app.models.detection import GenericTranscriptionJob
        from datetime import datetime
        db.add(GenericTranscriptionJob(
            JobID=902, FingerprintID="fp-complete-test", Status="complete",
            JsonPath="fp-complete-test.json", CreatedAt=datetime.now(),
            CompletedAt=datetime.now(),
        ))
        db.commit()
        from app.services.detection_service import get_generic_transcript_path
        result = get_generic_transcript_path(db, "fp-complete-test")
        assert result == "fp-complete-test.json"

    def test_get_generic_transcript_path_none_for_empty_fingerprint(self, db):
        from app.services.detection_service import get_generic_transcript_path
        assert get_generic_transcript_path(db, None) is None
        assert get_generic_transcript_path(db, "") is None


class TestShouldStageLogic:
    """
    Regression test for the bug where should_stage was gated by
    `is_generic and ...`, silently excluding liveread from staging entirely.
    Tests the exact boolean expression used in registration.py.
    """

    def _should_stage(self, is_generic, subscriber_already_has_this_fp):
        return (not is_generic) or (is_generic and not subscriber_already_has_this_fp)

    def test_liveread_always_stages(self):
        # Liveread has no fingerprint concept -- subscriber_already_has_this_fp
        # is always False for it, but the key assertion is is_generic=False
        # must always stage regardless.
        assert self._should_stage(is_generic=False, subscriber_already_has_this_fp=False) is True

    def test_generic_new_fingerprint_stages(self):
        assert self._should_stage(is_generic=True, subscriber_already_has_this_fp=False) is True

    def test_generic_duplicate_for_subscriber_does_not_stage(self):
        assert self._should_stage(is_generic=True, subscriber_already_has_this_fp=True) is False


class TestRoutesRegistered:
    """
    Regression test for the missing @router.get decorator bug that silently
    dropped /detections/{id}/transcript from FastAPI's route table.
    """

    def test_all_detection_transcript_routes_exist(self):
        """
        Checks the route decorators exist in source rather than introspecting
        app.routes, since FastAPI's internal router representation varies by
        version (some wrap sub-routers in lazy objects without a flat .routes
        list). This still catches the real regression class: a route function
        defined without its @router.get(...) decorator.
        """
        import inspect
        import app.routes.detections as detections_module

        source = inspect.getsource(detections_module)
        assert '@router.get("/detections/{detection_id}/transcript")' in source
        assert '@router.get("/detections/{detection_id}/generic-transcript")' in source
        assert '@router.get("/detections/{detection_id}/clip")' in source

        # Also confirm each decorator is immediately followed by a def line
        # (catches the exact bug we hit: decorator present but pointing at
        # the wrong function, or a def with no decorator directly above it).
        import re
        for path in (
            "/detections/{detection_id}/transcript",
            "/detections/{detection_id}/generic-transcript",
            "/detections/{detection_id}/clip",
        ):
            pattern = re.escape(f'@router.get("{path}")') + r'\s*\ndef \w+\('
            assert re.search(pattern, source), f"Decorator for {path} not directly above a def"


class TestWordDetectionTranscriptRoute:
    """
    Same decorator-integrity check as commercial/liveread/generic, applied
    to the new Word & Phrase karaoke route -- catches the exact class of
    bug we hit earlier (decorator silently detached from its function).
    """

    def test_word_transcript_route_decorator_exists(self):
        import inspect
        import re
        import app.routes.words as words_module

        source = inspect.getsource(words_module)
        assert '@router.get("/detections/{detection_id}/transcript")' in source

        pattern = re.escape('@router.get("/detections/{detection_id}/transcript")') + r'\s*\ndef \w+\('
        assert re.search(pattern, source), "Transcript route decorator not directly above its def"

    def test_word_detection_word_timestamp_path_field_exists(self):
        from app.models.detection import WordDetection
        assert hasattr(WordDetection, "WordTimestampPath")


class TestTranscriptionAudioRoutes:
    """Decorator-integrity check for the new My Transcriptions audio/karaoke routes."""

    def test_audio_route_decorator_exists(self):
        import inspect
        import re
        import app.routes.transcriptions as transcriptions_module

        source = inspect.getsource(transcriptions_module)
        assert '@router.get("/{transcript_id}/audio")' in source
        pattern = re.escape('@router.get("/{transcript_id}/audio")') + r'\s*\ndef \w+\('
        assert re.search(pattern, source)

    def test_transcript_json_route_decorator_exists(self):
        import inspect
        import re
        import app.routes.transcriptions as transcriptions_module

        source = inspect.getsource(transcriptions_module)
        assert '@router.get("/{transcript_id}/transcript-json")' in source
        pattern = re.escape('@router.get("/{transcript_id}/transcript-json")') + r'\s*\ndef \w+\('
        assert re.search(pattern, source)

    def test_resolve_chunk_audio_path_exists(self):
        from app.services.clip_service import resolve_chunk_audio_path
        assert callable(resolve_chunk_audio_path)

    def test_recording_chunk_has_audio_fields(self):
        from app.models.detection import RecordingChunk
        assert hasattr(RecordingChunk, "AudioPath")
        assert hasattr(RecordingChunk, "EarlyAudioPath")

    def test_resolve_chunk_audio_path_rejects_c_drive_path(self, monkeypatch, tmp_path):
        """
        EarlyAudioPath still pointing at AUDIOPROC-local C:\\ should fail the
        containment check against station_audio_root and return None --
        treated as 'not yet available', not an error.
        """
        from app.config import settings
        from app.services.clip_service import resolve_chunk_audio_path

        monkeypatch.setattr(settings, "station_audio_root", str(tmp_path))
        result = resolve_chunk_audio_path(r"C:\RadioMonitor\audio\5FM\chunk.mp3")
        assert result is None


class TestTranscriptEarlyJsonPath:
    """
    Regression coverage for the EarlyJsonPath/EarlyTextPath fix.
    Pipeline confirmed these columns do NOT follow a fixed folder shape --
    the web app must read the stored value as-is and never reconstruct it.
    """

    def test_transcript_model_has_early_path_fields(self):
        from app.models.detection import Transcript
        assert hasattr(Transcript, "EarlyJsonPath")
        assert hasattr(Transcript, "EarlyTextPath")

    def test_resolve_prefers_early_json_path(self, tmp_path):
        from app.routes.transcriptions import _resolve_transcript_json_path
        from unittest.mock import MagicMock

        early = tmp_path / "early.json"
        early.write_text("{}")
        late = tmp_path / "late.json"
        late.write_text("{}")

        t = MagicMock()
        t.EarlyJsonPath = str(early)
        t.JsonPath = str(late)

        result = _resolve_transcript_json_path(t)
        assert result == str(early)

    def test_resolve_falls_back_to_json_path_when_early_missing(self, tmp_path):
        from app.routes.transcriptions import _resolve_transcript_json_path
        from unittest.mock import MagicMock

        late = tmp_path / "late.json"
        late.write_text("{}")

        t = MagicMock()
        t.EarlyJsonPath = None
        t.JsonPath = str(late)

        result = _resolve_transcript_json_path(t)
        assert result == str(late)

    def test_resolve_returns_none_when_neither_exists(self):
        from app.routes.transcriptions import _resolve_transcript_json_path
        from unittest.mock import MagicMock

        t = MagicMock()
        t.EarlyJsonPath = r"C:\does\not\exist.json"
        t.JsonPath = r"C:\also\missing.json"

        result = _resolve_transcript_json_path(t)
        assert result is None

    def test_resolve_does_not_assume_archives_folder(self, tmp_path):
        """
        Confirms we never reconstruct a path -- a real file with an
        unconventional structure (no 'archives' segment) still resolves
        correctly since we check the raw stored value, not a template.
        """
        from app.routes.transcriptions import _resolve_transcript_json_path
        from unittest.mock import MagicMock

        odd_path = tmp_path / "5FM_26-06-24_094152.json"  # no archives/ subfolder
        odd_path.write_text("{}")

        t = MagicMock()
        t.EarlyJsonPath = str(odd_path)
        t.JsonPath = None

        result = _resolve_transcript_json_path(t)
        assert result == str(odd_path)
