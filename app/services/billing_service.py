"""
app/services/billing_service.py

Converts token costs to ZAR / USD for display. All prices are display-only;
the actual debit mechanism remains token-based (token_service.debit()).

Key functions:
    get_rate(db, plan_code)         -- ZARPerToken for a plan
    get_exchange_rate(db)           -- ZAR per 1 USD
    tokens_to_zar(tokens, rate)     -- Decimal ZAR value
    tokens_to_usd(tokens, rate, fx) -- Decimal USD value
    format_zar(amount)              -- 'R 3.60'
    format_usd(amount)              -- '$ 0.19'
    cost_display(db, tokens, plan)  -- full display dict for templates
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy.orm import Session

DEFAULT_ZAR_PER_TOKEN = Decimal("3.60")
DEFAULT_FX_RATE = Decimal("18.50")  # ZAR per 1 USD


def get_rate(db: Session, plan_code: str) -> Decimal:
    from app.models.billing import BillingConfig
    row = db.query(BillingConfig).filter(BillingConfig.PlanCode == plan_code).first()
    return row.ZARPerToken if row else DEFAULT_ZAR_PER_TOKEN


def get_exchange_rate(db: Session) -> Decimal:
    from app.models.billing import ExchangeRate
    row = db.query(ExchangeRate).filter(ExchangeRate.CurrencyPair == "ZAR/USD").first()
    return row.Rate if row else DEFAULT_FX_RATE


def tokens_to_zar(tokens: Decimal, zar_per_token: Decimal) -> Decimal:
    return (tokens * zar_per_token).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tokens_to_usd(tokens: Decimal, zar_per_token: Decimal, fx_rate: Decimal) -> Decimal:
    zar = tokens_to_zar(tokens, zar_per_token)
    return (zar / fx_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_zar(amount: Decimal) -> str:
    return f"R {amount:,.2f}"


def format_usd(amount: Decimal) -> str:
    return f"$ {amount:,.2f}"


def cost_display(
    db: Session,
    tokens: Decimal,
    plan_code: str,
    currency: str = "ZAR",
) -> dict:
    """
    Returns a dict suitable for template rendering:
        {
            "tokens":     "720.0000",
            "zar":        "R 2,592.00",
            "usd":        "$ 140.11",
            "primary":    "R 2,592.00",   # whichever currency is selected
            "secondary":  "$ 140.11",
            "currency":   "ZAR",
        }
    """
    rate = get_rate(db, plan_code)
    fx = get_exchange_rate(db)
    zar = tokens_to_zar(tokens, rate)
    usd = tokens_to_usd(tokens, rate, fx)
    primary = format_zar(zar) if currency == "ZAR" else format_usd(usd)
    secondary = format_usd(usd) if currency == "ZAR" else format_zar(zar)
    return {
        "tokens": str(tokens),
        "zar": format_zar(zar),
        "usd": format_usd(usd),
        "primary": primary,
        "secondary": secondary,
        "currency": currency,
    }


def action_cost_matrix(db: Session) -> list[dict]:
    """
    Builds the per-plan × per-action cost grid for the admin billing page.
    Uses token_service.calculate_cost() for a standard 1-station, 30-day
    registration as the reference unit cost per action type.
    """
    from decimal import Decimal
    from datetime import date, timedelta
    from app.services.token_service import calculate_cost
    from app.models.subscription_plan import SubscriptionPlanConfig
    from app.models.billing import BillingConfig

    plans = db.query(SubscriptionPlanConfig).filter(
        SubscriptionPlanConfig.IsActive == True  # noqa: E712
    ).order_by(SubscriptionPlanConfig.PlanCode).all()

    rates = {r.PlanCode: r.ZARPerToken for r in db.query(BillingConfig).all()}
    fx = get_exchange_rate(db)

    today = date.today()
    end = today + timedelta(days=29)

    # Reference: 1 station, 30 days (standard unit for display)
    base_tokens = calculate_cost(1, today, end)

    rows = []
    for plan in plans:
        rate = rates.get(plan.PlanCode, DEFAULT_ZAR_PER_TOKEN)
        zar = tokens_to_zar(base_tokens, rate)
        usd = tokens_to_usd(base_tokens, rate, fx)
        rows.append({
            "plan": plan.PlanCode,
            "display_name": plan.DisplayName,
            "zar_per_token": format_zar(rate),
            "tokens_per_unit": str(base_tokens),
            "zar_per_unit": format_zar(zar),
            "usd_per_unit": format_usd(usd),
        })
    return rows
