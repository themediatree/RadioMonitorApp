"""Tests for Settings.sample_dir() path resolution and safety guards."""

import pytest

from app.config import Settings


@pytest.fixture
def s():
    return Settings(_env_file=None, db_password="x", jwt_secret="y" * 40)


class TestSampleDirResolution:
    def test_generic_path(self, s):
        import ntpath
        assert s.sample_dir("Metro_FM", "generic") == \
            ntpath.join(s.samples_staging_root, "Metro_FM", "generic")

    def test_liveread_path(self, s):
        import ntpath
        assert s.sample_dir("Metro_FM", "liveread") == \
            ntpath.join(s.samples_staging_root, "Metro_FM", "liveread")

    def test_category_case_insensitive(self, s):
        assert s.sample_dir("X", "GENERIC") == s.sample_dir("X", "generic")

    def test_station_whitespace_trimmed(self, s):
        import ntpath
        assert s.sample_dir("  Metro_FM  ", "generic") == \
            ntpath.join(s.samples_staging_root, "Metro_FM", "generic")


class TestSampleDirGuards:
    @pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b", "C:", "../x"])
    def test_unsafe_station_rejected(self, s, bad):
        with pytest.raises(ValueError, match="Unsafe station name"):
            s.sample_dir(bad, "generic")

    def test_unknown_category_rejected(self, s):
        with pytest.raises(ValueError, match="Unknown sample category"):
            s.sample_dir("Metro_FM", "songs")
