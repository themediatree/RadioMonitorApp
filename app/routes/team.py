"""
Team management routes (subscriber_admin only).

    GET  /account/team                       list users in own Subscriber
    GET  /account/team/{user_id}/permissions  edit one user's permissions
    POST /account/team/{user_id}/permissions  save station restrictions + download right

Only a subscriber_admin may access these, and only for users within their
own Subscriber. Internal staff have no equivalent UI here -- by design,
this is the tenant's own self-service permission management, not something
internal staff configure on a tenant's behalf.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.user import User, UserType
from app.services import permission_service
from app.services.permission_service import PermissionError
from app.templating import templates

router = APIRouter(prefix="/account/team", tags=["team"])


def _require_subscriber_admin(user: User) -> None:
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        raise PermissionError("Only a Subscriber admin can manage team permissions.")


@router.get("", response_class=HTMLResponse)
def team_list(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    members = permission_service.get_team_members(db, user)

    return templates.TemplateResponse(
        request=request,
        name="app/team/list.html",
        context={
            "user": user,
            "members": members,
            **right_panel_context(user, db, request),
        },
    )


@router.get("/{user_id}/permissions", response_class=HTMLResponse)
def edit_permissions_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: int,
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    target = db.get(User, user_id)
    if target is None or target.SubscriberID != user.SubscriberID:
        return RedirectResponse("/account/team", status_code=status.HTTP_303_SEE_OTHER)

    from app.services.permission_service import get_user_station_ids, visible_stations
    current_station_ids = get_user_station_ids(db, target)
    all_stations = visible_stations(db, user)  # admin's own view -- always unrestricted

    return templates.TemplateResponse(
        request=request,
        name="app/team/permissions.html",
        context={
            "user": user,
            "target": target,
            "all_stations": all_stations,
            "current_station_ids": current_station_ids,
            "error": None,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/{user_id}/permissions")
async def save_permissions(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: int,
    restriction_mode: Annotated[str, Form()] = "unrestricted",
    can_download: Annotated[str, Form()] = "",
):
    if user.user_type != UserType.SUBSCRIBER_ADMIN:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    target = db.get(User, user_id)
    if target is None or target.SubscriberID != user.SubscriberID:
        return RedirectResponse("/account/team", status_code=status.HTTP_303_SEE_OTHER)

    raw_form = await request.form()
    station_ids = [int(s) for s in raw_form.getlist("station_ids") if s.isdigit()]

    try:
        if restriction_mode == "restricted":
            permission_service.set_station_restrictions(db, user, target, station_ids)
        else:
            permission_service.clear_station_restrictions(db, user, target)

        permission_service.set_can_download(db, user, target, can_download == "on")
        db.commit()
    except PermissionError as e:
        from app.services.permission_service import get_user_station_ids, visible_stations
        return templates.TemplateResponse(
            request=request,
            name="app/team/permissions.html",
            context={
                "user": user,
                "target": target,
                "all_stations": visible_stations(db, user),
                "current_station_ids": get_user_station_ids(db, target),
                "error": str(e),
                **right_panel_context(user, db, request),
            },
            status_code=400,
        )

    return RedirectResponse("/account/team", status_code=status.HTTP_303_SEE_OTHER)
