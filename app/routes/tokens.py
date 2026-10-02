"""
Token account routes.

    GET  /account/tokens              balance + recent transactions (subscriber view)
    GET  /account/tokens/history      full transaction history (paginated)
    GET  /admin/tokens                all accounts (internal only)
    POST /admin/tokens/{subscriber_id}/credit   manually credit tokens (internal only)
"""

import math
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context, require_internal
from app.models.billing import SubscriberTokenAccount
from app.models.subscriber import Subscriber
from app.models.user import User, UserType
from app.services import audit_service
from app.services.token_service import (
    credit,
    get_balance,
    get_or_create_account,
    list_transactions,
)
from app.templating import templates

router = APIRouter(tags=["tokens"])


def _ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


@router.get("/account/tokens", response_class=HTMLResponse)
def token_balance(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if user.user_type == UserType.INTERNAL:
        return RedirectResponse("/admin/tokens", status_code=302)

    if user.SubscriberID is None:
        return RedirectResponse("/dashboard", status_code=302)

    account = get_or_create_account(db, user.SubscriberID)
    recent_txs, _ = list_transactions(db, user.SubscriberID, page_size=10)

    from app.services.billing_service import (
        get_rate, get_exchange_rate, tokens_to_zar, tokens_to_usd, format_zar, format_usd
    )
    from app.deps import get_currency
    from decimal import Decimal

    # Get plan code for subscriber
    from app.models.subscriber import Subscriber
    subscriber = db.get(Subscriber, user.SubscriberID)
    plan_code = subscriber.SubscriptionPlan if subscriber else "standard"

    rate = get_rate(db, plan_code)
    fx = get_exchange_rate(db)
    balance_tokens = Decimal(str(account.TokenBalance or 0))

    currency = get_currency(request)
    is_postpaid = subscriber.BillingMode == "postpaid" if subscriber else False

    return templates.TemplateResponse(
        request=request,
        name="app/tokens/balance.html",
        context={
            "user": user,
            "account": account,
            "recent_txs": recent_txs,
            "balance_zar": format_zar(tokens_to_zar(balance_tokens, rate)),
            "balance_usd": format_usd(tokens_to_usd(balance_tokens, rate, fx)),
            "purchased_zar": format_zar(tokens_to_zar(Decimal(str(account.TotalPurchased or 0)), rate)),
            "purchased_usd": format_usd(tokens_to_usd(Decimal(str(account.TotalPurchased or 0)), rate, fx)),
            "consumed_zar": format_zar(tokens_to_zar(Decimal(str(account.TotalConsumed or 0)), rate)),
            "consumed_usd": format_usd(tokens_to_usd(Decimal(str(account.TotalConsumed or 0)), rate, fx)),
            "currency": currency,
            "is_postpaid": is_postpaid,
            "zar_per_token": float(rate),
            "fx_rate": float(fx),
            **right_panel_context(user, db, request),
        },
    )


@router.get("/account/tokens/history", response_class=HTMLResponse)
def token_history(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
):
    if user.SubscriberID is None:
        return RedirectResponse("/dashboard", status_code=302)

    txs, total = list_transactions(db, user.SubscriberID, page=page)
    total_pages = max(1, math.ceil(total / 50))

    from app.services.billing_service import get_rate, get_exchange_rate, tokens_to_zar, tokens_to_usd, format_zar, format_usd
    from app.deps import get_currency
    from app.models.subscriber import Subscriber
    sub = db.get(Subscriber, user.SubscriberID)
    plan_code = sub.SubscriptionPlan if sub else "standard"
    rate = get_rate(db, plan_code)
    fx = get_exchange_rate(db)
    currency = get_currency(request)
    is_postpaid = sub.BillingMode == "postpaid" if sub else False

    return templates.TemplateResponse(
        request=request,
        name="app/tokens/history.html",
        context={
            "user": user,
            "txs": txs,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "zar_per_token": float(rate),
            "fx_rate": float(fx),
            "currency": currency,
            "is_postpaid": is_postpaid,
            **right_panel_context(user, db, request),
        },
    )


@router.get("/admin/tokens", response_class=HTMLResponse)
def admin_token_overview(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
):
    accounts = (
        db.query(SubscriberTokenAccount)
        .join(Subscriber, Subscriber.SubscriberID == SubscriberTokenAccount.SubscriberID)
        .order_by(Subscriber.Name)
        .all()
    )
    subscribers = (
        db.query(Subscriber)
        .filter(Subscriber.SubscriberStatus == "active")
        .order_by(Subscriber.Name)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/tokens.html",
        context={
            "user": user,
            "accounts": accounts,
            "subscribers": subscribers,
        },
    )


@router.post("/admin/tokens/{subscriber_id}/credit")
def admin_credit_tokens(
    request: Request,
    user: Annotated[User, Depends(require_internal)],
    db: Annotated[Session, Depends(get_db)],
    subscriber_id: int,
    amount: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    tx_type: Annotated[str, Form()] = "purchase",
):
    sub = db.get(Subscriber, subscriber_id)
    if sub is None:
        return RedirectResponse("/admin/tokens", status_code=303)

    try:
        token_amount = Decimal(amount.strip())
        if token_amount <= 0:
            raise ValueError
    except (ValueError, Exception):
        return RedirectResponse("/admin/tokens?error=invalid_amount", status_code=303)

    valid_types = ("purchase", "credit", "adjustment")
    if tx_type not in valid_types:
        tx_type = "purchase"

    credit(
        db, subscriber_id, token_amount,
        description=description.strip() or f"Manual credit by {user.Email}",
        tx_type=tx_type,
        created_by_user_id=user.UserID,
    )
    db.commit()

    audit_service.record(
        db,
        action="tokens_credited",
        actor_user_id=user.UserID,
        target_subscriber_id=subscriber_id,
        details=f"amount={token_amount} type={tx_type} description={description}",
        ip_address=_ip(request),
    )

    return RedirectResponse("/admin/tokens", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/account/credits/purchase", response_class=HTMLResponse)
def purchase_credits(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.deps import right_panel_context
    from app.models.subscriber import Subscriber
    # Postpaid subscribers are invoiced — there is nothing to purchase.
    sub = db.get(Subscriber, user.SubscriberID) if user.SubscriberID else None
    if sub and sub.BillingMode == "postpaid":
        return RedirectResponse("/account/tokens", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="app/tokens/purchase.html",
        context={"user": user, **right_panel_context(user, db, request)},
    )
