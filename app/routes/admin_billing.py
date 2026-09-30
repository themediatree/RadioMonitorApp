"""
Admin billing routes.

    GET  /admin/billing                               pricing overview + edit form
    POST /admin/billing/rates                         update per-plan ZAR token prices
    POST /admin/billing/fx                            update ZAR/USD exchange rate
    GET  /admin/subscribers/{id}/service-rates        per-subscriber service rate overrides
    POST /admin/subscribers/{id}/service-rates        save overrides
"""

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_internal
from app.models.billing import BillingConfig, ExchangeRate, SubscriberServiceRate
from app.models.subscription_plan import SubscriptionPlanConfig
from app.models.user import User
from app.services.billing_service import action_cost_matrix, get_exchange_rate
from app.services.token_service import DEFAULT_SERVICE_RATES, get_effective_rate
from app.templating import templates

router = APIRouter(tags=["admin-billing"])


@router.get("/admin/billing", response_class=HTMLResponse)
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


@router.post("/admin/billing/rates")
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


@router.post("/admin/billing/fx")
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


# ── Per-subscriber service rate overrides ────────────────────────────────────

SERVICE_LABELS = {
    "commercial":    "Commercial / Ad spot",
    "song":          "Song / Music tracking",
    "word":          "Keyword / Word monitoring",
    "transcription": "Transcription",
    "spectrum":      "Spectrum / Signal monitoring",
}


@router.get("/admin/subscribers/{subscriber_id}/service-rates", response_class=HTMLResponse)
def subscriber_service_rates(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
):
    from app.models.subscriber import Subscriber

    sub = db.query(Subscriber).filter(Subscriber.SubscriberID == subscriber_id).first()
    if sub is None:
        return RedirectResponse("/admin/subscribers", status_code=303)

    overrides = {
        r.ServiceType: r
        for r in db.query(SubscriberServiceRate)
        .filter(SubscriberServiceRate.SubscriberID == subscriber_id)
        .all()
    }

    services = [
        {
            "key": svc,
            "label": SERVICE_LABELS.get(svc, svc),
            "default": DEFAULT_SERVICE_RATES.get(svc, Decimal("1.00")),
            "override": overrides.get(svc),
            "effective": get_effective_rate(db, subscriber_id, svc),
        }
        for svc in DEFAULT_SERVICE_RATES
    ]

    return templates.TemplateResponse(
        request=request,
        name="admin/subscriber_service_rates.html",
        context={
            "user": user,
            "subscriber": sub,
            "services": services,
            "saved": request.query_params.get("saved", ""),
        },
    )


@router.post("/admin/subscribers/{subscriber_id}/service-rates")
async def save_subscriber_service_rates(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
):
    from app.models.subscriber import Subscriber

    sub = db.query(Subscriber).filter(Subscriber.SubscriberID == subscriber_id).first()
    if sub is None:
        return RedirectResponse("/admin/subscribers", status_code=303)

    form = await request.form()
    errors = []

    for svc in DEFAULT_SERVICE_RATES:
        raw_rate = (form.get(f"rate_{svc}") or "").strip()
        clear = form.get(f"clear_{svc}") == "1"

        existing = (
            db.query(SubscriberServiceRate)
            .filter(
                SubscriberServiceRate.SubscriberID == subscriber_id,
                SubscriberServiceRate.ServiceType == svc,
            )
            .first()
        )

        if clear:
            if existing:
                db.delete(existing)
            continue

        if not raw_rate:
            continue  # field left blank → no change

        try:
            rate = Decimal(raw_rate)
            if rate < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            errors.append(f"Invalid rate for {SERVICE_LABELS.get(svc, svc)}: '{raw_rate}'")
            continue

        notes = (form.get(f"notes_{svc}") or "").strip() or None

        if existing:
            existing.RatePerHour = rate
            existing.SetByUserID = user.UserID
            existing.Notes = notes
        else:
            db.add(SubscriberServiceRate(
                SubscriberID=subscriber_id,
                ServiceType=svc,
                RatePerHour=rate,
                SetByUserID=user.UserID,
                Notes=notes,
            ))

    if not errors:
        db.commit()
        saved = "ok"
    else:
        db.rollback()
        saved = "error"

    return RedirectResponse(
        f"/admin/subscribers/{subscriber_id}/service-rates?saved={saved}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
