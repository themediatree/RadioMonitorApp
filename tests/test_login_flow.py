"""
End-to-end test of the login flow against an in-memory SQLite DB.

We monkey-patch the engine to use SQLite, create the tables from the
models, seed a user, and exercise the full HTTP flow with FastAPI's
TestClient.

Note: this is a partial test only -- some SQL Server idioms (the
SET TRANSACTION ISOLATION LEVEL listener, the IDENTITY column behavior)
don't fully apply to SQLite. The point is to prove the app's
auth/cookie/render plumbing works.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="module")
def app_with_sqlite(monkeymodule):
    """Bootstrap the app against an in-memory SQLite database."""
    from sqlalchemy.pool import StaticPool

    # StaticPool + check_same_thread=False keeps one connection that all
    # sessions share, so the in-memory DB persists across requests.
    sqlite_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # Reach into app.database and swap engine + sessionmaker BEFORE the
    # routes import them transitively. main.py is fine because it doesn't
    # use the engine at import time.
    import app.database as db_module
    db_module.engine = sqlite_engine
    db_module.SessionLocal = sessionmaker(
        bind=sqlite_engine, autoflush=False, autocommit=False,
        expire_on_commit=False, future=True,
    )

    # Ensure all models are registered, then create tables
    import app.models  # noqa: F401
    db_module.Base.metadata.create_all(sqlite_engine)

    # Force the FK pragma in SQLite (off by default)
    with sqlite_engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(text("PRAGMA foreign_keys = ON"))

    # Now import the app
    from main import app
    yield app


@pytest.fixture(scope="module")
def monkeymodule():
    """A pytest monkeypatch fixture with module scope."""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def seeded_user(app_with_sqlite):
    """Insert one internal user we can log in as."""
    from app.auth.password import hash_password
    from app.database import session_scope
    from app.models.user import User, UserType

    with session_scope() as db:
        u = User(
            Email="admin@example.com",
            PasswordHash=hash_password("hunter2hunter2"),
            FullName="Admin User",
            UserType=UserType.INTERNAL.value,
            IsActive=True,
            EmailVerified=True,
        )
        db.add(u)
    return "admin@example.com"


@pytest.fixture(scope="module")
def client(app_with_sqlite):
    from fastapi.testclient import TestClient
    return TestClient(app_with_sqlite)


class TestPublicRoutes:
    def test_landing_loads_unauthenticated(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "NOCTIV" in r.text
        assert "Sign in" in r.text

    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_login_form_renders(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert "Sign in" in r.text
        assert 'name="email"' in r.text
        assert 'name="password"' in r.text


class TestProtectedRoutes:
    def test_dashboard_redirects_when_unauthenticated(self, client):
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"].startswith("/login")

    def test_api_me_returns_401_without_auth(self, client):
        r = client.get("/api/me")
        assert r.status_code == 401


class TestLoginFlow:
    def test_wrong_password_rerenders_form(self, client, seeded_user):
        r = client.post(
            "/login",
            data={"email": seeded_user, "password": "wrongpassword11"},
        )
        # Form re-renders with 401 status and error message
        assert r.status_code == 401
        assert "Invalid email or password" in r.text

    def test_unknown_email_rerenders_form(self, client, seeded_user):
        r = client.post(
            "/login",
            data={"email": "nobody@example.com", "password": "anything12345"},
        )
        assert r.status_code == 401

    def test_successful_login_sets_cookie_and_redirects(self, client, seeded_user):
        r = client.post(
            "/login",
            data={"email": seeded_user, "password": "hunter2hunter2"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        # Cookie should be set
        from app.config import settings
        assert settings.session_cookie_name in r.cookies

    def test_dashboard_accessible_after_login(self, client, seeded_user):
        # Log in -- TestClient persists cookies
        client.post(
            "/login",
            data={"email": seeded_user, "password": "hunter2hunter2"},
        )
        r = client.get("/dashboard")
        assert r.status_code == 200
        assert "Welcome" in r.text

    def test_api_me_works_after_login(self, client, seeded_user):
        client.post(
            "/login",
            data={"email": seeded_user, "password": "hunter2hunter2"},
        )
        r = client.get("/api/me")
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == seeded_user
        assert body["user_type"] == "internal"
        assert body["subscriber_id"] is None

    def test_logout_clears_cookie(self, client, seeded_user):
        client.post(
            "/login",
            data={"email": seeded_user, "password": "hunter2hunter2"},
        )
        r = client.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        # After logout, dashboard redirects to login
        r2 = client.get("/dashboard", follow_redirects=False)
        assert r2.status_code == 303
        assert r2.headers["location"].startswith("/login")


class TestJsonAuthApi:
    def test_api_login_returns_token(self, client, seeded_user):
        # Use a fresh client to avoid cookie carry-over from earlier tests
        from fastapi.testclient import TestClient
        from main import app
        fresh = TestClient(app)
        r = fresh.post(
            "/api/login",
            json={"email": seeded_user, "password": "hunter2hunter2"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_api_login_with_bad_password_returns_401(self, client, seeded_user):
        from fastapi.testclient import TestClient
        from main import app
        fresh = TestClient(app)
        r = fresh.post(
            "/api/login",
            json={"email": seeded_user, "password": "wrongpassword11"},
        )
        assert r.status_code == 401

    def test_bearer_token_works_in_authorization_header(self, client, seeded_user):
        from fastapi.testclient import TestClient
        from main import app
        fresh = TestClient(app)
        # Clear cookies first to make sure we're testing the header path
        login_resp = fresh.post(
            "/api/login",
            json={"email": seeded_user, "password": "hunter2hunter2"},
        )
        token = login_resp.json()["access_token"]
        fresh.cookies.clear()
        r = fresh.get(
            "/api/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["email"] == seeded_user
