"""
RadioMonitor web application -- entry point.

Run locally:
    uvicorn main:app --reload

Or simply:
    python main.py
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import check_connection
from app.scheduler import setup_scheduler, shutdown_scheduler
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import commercials as commercial_routes
from app.routes import dashboard as dashboard_routes
from app.routes import detections as detection_routes
from app.routes import invitations as invitation_routes
from app.routes import pages as pages_routes
from app.routes import registration as registration_routes
from app.routes import songs as song_routes
from app.routes import tokens as token_routes
from app.routes import transcriptions as transcription_routes
from app.routes import reports as report_routes
from app.routes import towers as tower_routes
from app.routes import team as team_routes
from app.routes import admin_billing as admin_billing_routes
from app.routes import api_pull as api_pull_routes
from app.routes import api_push as api_push_routes
from app.routes import api_keys as api_keys_routes
from app.routes import words as word_routes
from app.routes import pi_devices as pi_device_routes
from app.templating import templates

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("radiomonitor")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    logger = logging.getLogger("radiomonitor.startup")

    log.info("Starting %s (env=%s)", settings.app_name, settings.app_env)

    # Security warnings
    if settings.jwt_secret_is_weak:
        log.warning(
            "SECURITY: JWT_SECRET is weak or default. "
            "Set a strong random secret (32+ chars) in .env before going live. "
            "Generate one: python -c 'import secrets; print(secrets.token_hex(32))'"
        )
    if not settings.session_cookie_secure:
        log.info("SESSION_COOKIE_SECURE is False. Set True when deploying with HTTPS.")

    if check_connection():
        log.info("Database connection OK -> %s", settings.db_name)
    else:
        log.error(
            "Database connection FAILED at startup. "
            "DB-backed routes will 500. Check DB_* in .env."
        )

    # Start midnight station sync scheduler
    setup_scheduler()

    yield

    log.info("Shutting down %s", settings.app_name)
    shutdown_scheduler()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.app_name,
    debug=settings.debug,
    lifespan=lifespan,
    # Don't expose Swagger/Redoc in production
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

from app.middleware.security import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)

# Static files
BASE_DIR = Path(__file__).resolve().parent
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)


# Make `now_year` available in every template
@app.middleware("http")
async def add_template_globals(request: Request, call_next):
    response = await call_next(request)
    return response


templates.env.globals["now_year"] = datetime.now(timezone.utc).year


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(pages_routes.router)
app.include_router(auth_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(invitation_routes.router)
app.include_router(admin_routes.router)
app.include_router(registration_routes.router)
app.include_router(detection_routes.router)
app.include_router(commercial_routes.router)
app.include_router(song_routes.router)
app.include_router(word_routes.router)
app.include_router(token_routes.router)
app.include_router(transcription_routes.router)
app.include_router(report_routes.router)
app.include_router(tower_routes.router)
app.include_router(team_routes.router)
app.include_router(admin_billing_routes.router)
app.include_router(api_pull_routes.router)
app.include_router(api_push_routes.router)
app.include_router(api_keys_routes.router)
app.include_router(pi_device_routes.router)


# ---------------------------------------------------------------------------
# Error handlers -- render HTML for browser, JSON for API
# ---------------------------------------------------------------------------
def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return (
        request.url.path.startswith("/api/")
        or "application/json" in accept
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # 401 from a browser request -> bounce to /login
    if exc.status_code == status.HTTP_401_UNAUTHORIZED and not _wants_json(request):
        from fastapi.responses import RedirectResponse
        reason = ""
        if getattr(exc, "detail", None) == "session_ended":
            reason = "?reason=session_ended"
        target = f"/login{reason}"
        if not reason and request.url.path not in ("/", "/login"):
            target = f"/login?next={request.url.path}"
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    template_for_status = {
        403: "errors/403.html",
        404: "errors/404.html",
    }
    template_name = template_for_status.get(exc.status_code, "errors/500.html")
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"user": None, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception on %s: %s", request.url.path, exc)
    if _wants_json(request):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )
    return templates.TemplateResponse(
        request=request,
        name="errors/500.html",
        context={"user": None},
        status_code=500,
    )


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
