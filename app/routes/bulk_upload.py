"""
Bulk upload routes.

    GET  /bulk-upload                  landing page with template download
    GET  /bulk-upload/template         downloads the Excel template
    POST /bulk-upload/validate         upload workbook + ZIP, validate, show cost preview
    POST /bulk-upload/commit           process previously-validated rows

Each valid row is processed through the EXACT SAME service functions the
single-entry forms call -- no parallel registration/subscription logic.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.station import Station
from app.models.user import User
from app.templating import templates

router = APIRouter(prefix="/bulk-upload", tags=["bulk-upload"])


def _active_stations(db: Session) -> list[Station]:
    return (
        db.query(Station)
        .filter(Station.IsActive == True)  # noqa: E712
        .filter(Station.StationID != 6)
        .order_by(Station.StationName)
        .all()
    )


@router.get("", response_class=HTMLResponse)
def bulk_upload_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="app/bulk_upload/index.html",
        context={"user": user, "error": None, **right_panel_context(user, db, request)},
    )


@router.get("/template")
def download_template(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.services.bulk_upload_service import build_template

    stations = _active_stations(db)
    station_names = [s.StationName for s in stations]
    data = build_template(db, station_names)

    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=NOCTIV_Bulk_Upload_Template.xlsx"},
    )


@router.post("/validate", response_class=HTMLResponse)
async def validate_upload(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    workbook: Annotated[UploadFile, File()],
    archive: Annotated[UploadFile, File()] = None,
):
    from app.services.bulk_upload_service import validate_workbook

    wb_bytes = await workbook.read()
    zip_bytes = await archive.read() if archive else None

    result = validate_workbook(db, user, wb_bytes, zip_bytes)

    if result.session_token is None:
        # Nothing valid to process at all -- still show the page so the
        # user can see exactly what failed.
        return templates.TemplateResponse(
            request=request,
            name="app/bulk_upload/preview.html",
            context={
                "user": user, "result": result, "can_commit": False,
                **right_panel_context(user, db, request),
            },
        )

    return templates.TemplateResponse(
        request=request,
        name="app/bulk_upload/preview.html",
        context={
            "user": user, "result": result, "can_commit": True,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/commit")
async def commit_upload(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    session_token: Annotated[str, ...] = None,
):
    from fastapi import Form
    from app.services.bulk_upload_service import commit_session

    raw_form = await request.form()
    token = raw_form.get("session_token")

    outcome = commit_session(db, user, token)
    db.commit()

    return templates.TemplateResponse(
        request=request,
        name="app/bulk_upload/result.html",
        context={
            "user": user, "outcome": outcome,
            **right_panel_context(user, db, request),
        },
    )
