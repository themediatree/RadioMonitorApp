"""Tests for per-user station permissions and download rights."""

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


def _make_subscriber_and_users(db, suffix):
    from app.models.subscriber import Subscriber
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.user import User, UserType
    from app.models.station import Station

    db.add(SubscriptionPlanConfig(PlanCode=f"perm{suffix}", DisplayName="Perm", IsActive=True))
    sub = Subscriber(Name=f"PermSub{suffix}", Slug=f"perm-sub-{suffix}",
                     SubscriberType="Agent", SubscriptionPlan=f"perm{suffix}",
                     SubscriberStatus="active")
    db.add(sub); db.flush()

    admin = User(Email=f"admin{suffix}@test.com", PasswordHash="x",
                SubscriberID=sub.SubscriberID, UserType=UserType.SUBSCRIBER_ADMIN, IsActive=True)
    member = User(Email=f"member{suffix}@test.com", PasswordHash="x",
                 SubscriberID=sub.SubscriberID, UserType=UserType.SUBSCRIBER_USER, IsActive=True)
    db.add_all([admin, member]); db.flush()

    s1 = Station(StationID=2000 + suffix, StationName=f"StationA{suffix}", IsActive=True)
    s2 = Station(StationID=3000 + suffix, StationName=f"StationB{suffix}", IsActive=True)
    db.add_all([s1, s2]); db.flush()
    db.commit()
    return sub, admin, member, s1, s2


class TestGetAllowedStationIds:
    def test_internal_user_always_unrestricted(self, db):
        from app.models.user import User, UserType
        u = User(Email="int@test.com", PasswordHash="x", UserType=UserType.INTERNAL, IsActive=True)
        from app.services.permission_service import get_allowed_station_ids
        assert get_allowed_station_ids(db, u) is None

    def test_subscriber_admin_always_unrestricted(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 1)
        from app.services.permission_service import get_allowed_station_ids
        assert get_allowed_station_ids(db, admin) is None

    def test_new_user_default_unrestricted(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 2)
        from app.services.permission_service import get_allowed_station_ids
        assert get_allowed_station_ids(db, member) is None  # grandfathered default

    def test_restricted_user_returns_explicit_set(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 3)
        from app.services.permission_service import set_station_restrictions, get_allowed_station_ids
        set_station_restrictions(db, admin, member, [s1.StationID])
        db.commit()
        allowed = get_allowed_station_ids(db, member)
        assert allowed == {s1.StationID}

    def test_restricted_to_zero_returns_empty_set_not_none(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 4)
        from app.services.permission_service import set_station_restrictions, get_allowed_station_ids
        set_station_restrictions(db, admin, member, [])
        db.commit()
        allowed = get_allowed_station_ids(db, member)
        assert allowed == set()  # explicit lockout, distinct from None (unrestricted)

    def test_clear_restrictions_returns_to_unrestricted(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 5)
        from app.services.permission_service import (
            set_station_restrictions, clear_station_restrictions, get_allowed_station_ids
        )
        set_station_restrictions(db, admin, member, [s1.StationID])
        db.commit()
        clear_station_restrictions(db, admin, member)
        db.commit()
        assert get_allowed_station_ids(db, member) is None


class TestCanDownload:
    def test_internal_always_true(self, db):
        from app.models.user import User, UserType
        u = User(Email="int2@test.com", PasswordHash="x", UserType=UserType.INTERNAL, IsActive=True)
        from app.services.permission_service import can_download
        assert can_download(u) is True

    def test_subscriber_admin_always_true(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 6)
        from app.services.permission_service import can_download
        assert can_download(admin) is True

    def test_new_member_default_true(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 7)
        from app.services.permission_service import can_download
        assert can_download(member) is True  # grandfathered

    def test_revoked_member_false(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 8)
        from app.services.permission_service import set_can_download, can_download
        set_can_download(db, admin, member, False)
        db.commit()
        assert can_download(member) is False


class TestAdminBoundaries:
    def test_subscriber_user_cannot_set_permissions(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 9)
        from app.services.permission_service import set_station_restrictions, PermissionError
        with pytest.raises(PermissionError):
            set_station_restrictions(db, member, member, [s1.StationID])

    def test_admin_cannot_restrict_user_in_different_subscriber(self, db):
        sub_a, admin_a, member_a, s1, s2 = _make_subscriber_and_users(db, 10)
        sub_b, admin_b, member_b, s3, s4 = _make_subscriber_and_users(db, 11)
        from app.services.permission_service import set_station_restrictions, PermissionError
        with pytest.raises(PermissionError):
            set_station_restrictions(db, admin_a, member_b, [s3.StationID])

    def test_cannot_restrict_an_admin_account(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 12)
        from app.services.permission_service import set_station_restrictions, PermissionError
        with pytest.raises(PermissionError):
            set_station_restrictions(db, admin, admin, [s1.StationID])


class TestVisibleStations:
    def test_unrestricted_sees_all_active_stations(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 13)
        from app.services.permission_service import visible_stations
        names = {s.StationName for s in visible_stations(db, member)}
        assert s1.StationName in names
        assert s2.StationName in names

    def test_restricted_sees_only_allowed(self, db):
        sub, admin, member, s1, s2 = _make_subscriber_and_users(db, 14)
        from app.services.permission_service import set_station_restrictions, visible_stations
        set_station_restrictions(db, admin, member, [s1.StationID])
        db.commit()
        names = {s.StationName for s in visible_stations(db, member)}
        assert names == {s1.StationName}


class TestTeamTemplateUsesCorrectProperty:
    """
    Regression test for the exact bug found: templates/app/team/list.html
    used the raw `m.UserType.value` (the plain string DB column, no .value
    attribute) instead of `m.user_type.value` (the lowercase property that
    correctly converts the string into a real UserType enum instance).
    This silently broke every role check on the Team page.
    """

    def test_team_list_does_not_use_raw_usertype_column(self):
        content = open("templates/app/team/list.html").read()
        assert ".UserType.value" not in content, (
            "Template uses the raw UserType column (no .value attribute) "
            "instead of the lowercase user_type property"
        )

    def test_team_list_uses_lowercase_property(self):
        content = open("templates/app/team/list.html").read()
        assert "user_type.value" in content


class TestAllPermissionServiceFunctionsImportable:
    """
    Regression test for the exact bug found: a str_replace edit consumed
    `def get_user_station_ids(...):` as part of its anchor text, silently
    deleting the function signature while leaving its body orphaned as
    dead code at the end of visible_stations(). The function "existed" as
    text in the file but was not actually defined -- any import of it
    raised ImportError at request time, not at deploy time.

    This test imports every public function the rest of the app actually
    calls by name, so a similarly-deleted definition fails the test suite
    immediately instead of surfacing as a 500 error days later.
    """

    def test_all_expected_functions_exist(self):
        from app.services.permission_service import (
            get_allowed_station_ids,
            can_download,
            set_station_restrictions,
            clear_station_restrictions,
            set_can_download,
            get_team_members,
            get_user_station_ids,
            visible_stations,
            PermissionError,
        )
        # Importing successfully is the test -- ImportError would fail it.
        assert all([
            callable(get_allowed_station_ids),
            callable(can_download),
            callable(set_station_restrictions),
            callable(clear_station_restrictions),
            callable(set_can_download),
            callable(get_team_members),
            callable(get_user_station_ids),
            callable(visible_stations),
        ])

    def test_team_routes_import_cleanly(self):
        """app/routes/team.py imports from permission_service at module load
        time -- if any referenced name is missing, this import itself fails."""
        import app.routes.team  # noqa: F401
