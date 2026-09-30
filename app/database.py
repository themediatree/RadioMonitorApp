"""
Database connection setup.

We use SQLAlchemy 2.0's sync engine with pyodbc. The pipeline already uses
SQL Server, so we connect to the same database the pipeline writes to.

Why sync over async:
    - pyodbc is sync-only. There IS aioodbc but it's a thin wrapper and the
      ecosystem around it is thinner.
    - FastAPI runs sync route handlers in a threadpool so this is fine for
      the expected concurrency (dozens, not thousands, of simultaneous users).
    - We can always migrate to asyncpg + Postgres later if scale demands it.

Usage in routes:
    from app.database import get_db
    @router.get("/something")
    def handler(db: Session = Depends(get_db)):
        ...
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def utc_now() -> datetime:
    """
    Naive UTC 'now'. Used as a Python-side default for timestamp columns.

    The schema also has SYSDATETIME() server defaults so raw SQL inserts
    (e.g. from the pipeline) still work. Setting BOTH ensures ORM inserts
    work on any backend (handy for tests against SQLite) AND raw inserts
    still fill the columns.
    """
    return datetime.now()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# pool_pre_ping = check connection liveness before handing it out. SQL Server
# kills idle connections; this prevents "connection closed" errors on the
# first query after a quiet period.
engine: Engine = create_engine(
    settings.sqlalchemy_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,
    echo=settings.debug and settings.app_env == "development",
    future=True,
)


# Force SQL Server to use READ COMMITTED SNAPSHOT-compatible isolation for
# read-only queries. The pipeline writes a lot; we want readers not to block
# writers. This is per-connection.
@event.listens_for(engine, "connect")
def _set_sqlserver_session_options(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    try:
        # Lets us read without blocking pipeline writers.
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


# ---------------------------------------------------------------------------
# Declarative base for all models
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """
    Yields a SQLAlchemy session and ensures it's closed when the request ends.
    Use as: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Standalone context manager for scripts (e.g. the seed script)
@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Provide a transactional scope for non-request code:

        with session_scope() as db:
            db.add(thing)
            # commits on success, rolls back on exception, closes always
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_connection() -> bool:
    """Quick check used at startup to verify the DB is reachable."""
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("DB connection check failed: %s", e)
        return False
