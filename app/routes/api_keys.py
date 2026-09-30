"""
API key management routes (subscriber_admin only).

    GET  /account/api          show current key + docs
    POST /account/api/generate create/rotate key
    POST /account/api/revoke   revoke key
"""

from typing import Annotated
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.user import User, UserType
from app.templating import templates

router = APIRouter(prefix="/account/api", tags=["api-keys"])


@router.get("", response_class=HTMLResponse)
def api_key_page(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    generated: str = "",
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=303)

    from app.services.api_key_service import get_active_key
    key_row = get_active_key(db, user.SubscriberID)

    return templates.TemplateResponse(
        request=request,
        name="app/api/key.html",
        context={
            "user": user,
            "key_row": key_row,
            "generated": generated,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/generate")
def generate_api_key(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=303)

    from app.services.api_key_service import create_api_key
    _, plaintext = create_api_key(db, user.SubscriberID, user.UserID)
    db.commit()

    # Store plaintext in flash-style query param (shown once)
    from urllib.parse import quote
    return RedirectResponse(
        f"/account/api?generated={quote(plaintext)}",
        status_code=303,
    )


@router.post("/revoke")
def revoke_api_key(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=303)

    from app.services.api_key_service import revoke_api_key as do_revoke
    do_revoke(db, user.SubscriberID)
    db.commit()

    return RedirectResponse("/account/api", status_code=303)
