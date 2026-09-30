"""
Admin billing routes.

    GET  /admin/billing          pricing overview + edit form
    POST /admin/billing/rates    update per-plan ZAR token prices
    POST /admin/billing/fx       update ZAR/USD exchange rate
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_internal
from app.models.billing import BillingConfig, ExchangeRate
from app.models.subscription_plan import SubscriptionPlanConfig
from app.models.user import User
from app.services.billing_service import action_cost_matrix, get_exchange_rate
from app.templating import templates

router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


@router.get("", response_class=HTMLResponse)
def billing_admin(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    saved: str = "",
):
    plans = db.query(SubscriptionPlanConfig).filter(
        SubscriptionPlanConfig.IsActive == True  # noqa: E712
    ).order_by(SubscriptionPlanConfig.PlanCode).all()

    rates = {r.PlanCode: r for r in db.query(BillingConfig).all()}
    fx = get_exchange_rate(db)
    matrix = action_cost_matrix(db)

    return templates.TemplateResponse(
        request=request,
        name="admin/billing.html",
        context={
            "user": user,
            "plans": plans,
            "rates": rates,
            "fx_rate": fx,
            "matrix": matrix,
            "saved": saved,
        },
    )


@router.post("/rates")
async def update_rates(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    form = await request.form()
    errors = []

    plans = db.query(SubscriptionPlanConfig).filter(
        SubscriptionPlanConfig.IsActive == True  # noqa: E712
    ).all()

    for plan in plans:
        raw = form.get(f"rate_{plan.PlanCode}", "")
        try:
            rate = Decimal(str(raw).strip())
            if rate <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            errors.append(f"Invalid rate for {plan.DisplayName}: '{raw}'")
            continue

        row = db.query(BillingConfig).filter(
            BillingConfig.PlanCode == plan.PlanCode
        ).first()
        if row:
            row.ZARPerToken = rate
            row.UpdatedByUserID = user.UserID
        else:
            db.add(BillingConfig(
                PlanCode=plan.PlanCode,
                ZARPerToken=rate,
                UpdatedByUserID=user.UserID,
            ))

    if not errors:
        db.commit()

    return RedirectResponse(
        f"/admin/billing?saved={'rates' if not errors else 'error'}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/fx")
async def update_fx(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    form = await request.form()
    raw = form.get("fx_rate", "")
    try:
        rate = Decimal(str(raw).strip())
        if rate <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        return RedirectResponse("/admin/billing?saved=error", status_code=303)

    row = db.query(ExchangeRate).filter(ExchangeRate.CurrencyPair == "ZAR/USD").first()
    if row:
        row.Rate = rate
        row.UpdatedByUserID = user.UserID
    else:
        db.add(ExchangeRate(CurrencyPair="ZAR/USD", Rate=rate, UpdatedByUserID=user.UserID))

    db.commit()
    return RedirectResponse("/admin/billing?saved=fx", status_code=303)
