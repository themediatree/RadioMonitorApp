"""
app/services/token_service.py

Token accounting for RadioMonitor's billing system (Phase B1).

Key operations:
  - get_or_create_account()   -- ensure a Subscriber has a token account
  - calculate_cost()          -- how many tokens a registration will cost
  - debit()                   -- deduct tokens at registration time
  - credit()                  -- add tokens (purchase, monthly allocation, trial)
  - get_balance()             -- current balance
  - list_transactions()       -- ledger history

Design: BILLING_DESIGN.md
1 token = 1 station-hour of monitoring
cost = stations_count × duration_hours
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.database import utc_now
from app.models.billing import (
    REFERENCE_TYPES,
    TRANSACTION_TYPES,
    SubscriberTokenAccount,
    TokenTransaction,
)
from app.models.subscriber import Subscriber
from app.models.subscription_plan import SubscriptionPlanConfig


class InsufficientTokensError(Exception):
    """Raised when a Subscriber's balance is too low, or a trial service cap is exceeded."""
    def __init__(self, required: Decimal, available: Decimal, message: Optional[str] = None):
        self.required = required
        self.available = available
        if message:
            super().__init__(message)
        else:
            super().__init__(
                f"Insufficient tokens. This registration requires {required:.2f} tokens; "
                f"your balance is {available:.2f}. "
                f"You need {(required - available):.2f} more tokens to proceed."
            )


class TokenServiceError(Exception):
    pass


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

def calculate_cost(
    station_count: int,
    start_date: date,
    end_date: Optional[date],
) -> Decimal:
    """
    Cost in tokens for a registration.
    1 token = 1 station-hour.
    Minimum duration: 24 hours (1 day).
    open-ended (end_date=None): charge 30 days upfront; re-assess on renewal.
    """
    if end_date is None:
        # Open-ended subscription: charge 30 days as initial commitment.
        duration_days = 30
    else:
        duration_days = max(1, (end_date - start_date).days + 1)

    duration_hours = Decimal(str(duration_days * 24))
    stations = Decimal(str(max(1, station_count)))
    cost = stations * duration_hours
    return cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def format_cost_preview(
    station_count: int,
    start_date: date,
    end_date: Optional[date],
) -> str:
    """Human-readable cost preview for form display."""
    cost = calculate_cost(station_count, start_date, end_date)
    if end_date is None:
        duration_label = "30 days (open-ended)"
    else:
        days = max(1, (end_date - start_date).days + 1)
        duration_label = f"{days} day{'s' if days != 1 else ''}"
    return (
        f"{station_count} station{'s' if station_count != 1 else ''} × "
        f"{duration_label} = {cost:.2f} tokens"
    )


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

def get_or_create_account(db: Session, subscriber_id: int) -> SubscriberTokenAccount:
    """Fetch or create the token account for a Subscriber."""
    account = (
        db.query(SubscriberTokenAccount)
        .filter(SubscriberTokenAccount.SubscriberID == subscriber_id)
        .one_or_none()
    )
    if account is None:
        account = SubscriberTokenAccount(
            SubscriberID=subscriber_id,
            TokenBalance=Decimal("0"),
            TotalPurchased=Decimal("0"),
            TotalConsumed=Decimal("0"),
            UpdatedAt=utc_now(),
        )
        db.add(account)
        db.flush()
    return account


def get_balance(db: Session, subscriber_id: int) -> Decimal:
    """Return current token balance. Returns 0 if no account exists."""
    account = (
        db.query(SubscriberTokenAccount)
        .filter(SubscriberTokenAccount.SubscriberID == subscriber_id)
        .one_or_none()
    )
    return account.TokenBalance if account else Decimal("0")


# ---------------------------------------------------------------------------
# Credit / debit
# ---------------------------------------------------------------------------

def _record_transaction(
    db: Session,
    account: SubscriberTokenAccount,
    *,
    tx_type: str,
    amount: Decimal,
    description: str,
    reference_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    source: str = "app",
) -> TokenTransaction:
    """Write a transaction row and update the account balance. Does NOT commit."""
    new_balance = account.TokenBalance + amount
    tx = TokenTransaction(
        SubscriberID=account.SubscriberID,
        Type=tx_type,
        TokenAmount=amount,
        BalanceAfter=new_balance,
        Description=description[:500] if description else None,
        ReferenceID=reference_id,
        ReferenceType=reference_type,
        CreatedByUserID=created_by_user_id,
        CreatedAt=utc_now(),
        Source=source,
    )
    db.add(tx)
    account.TokenBalance = new_balance
    account.UpdatedAt = utc_now()
    db.flush()
    return tx


def credit(
    db: Session,
    subscriber_id: int,
    amount: Decimal,
    *,
    description: str,
    tx_type: str = "credit",
    created_by_user_id: Optional[int] = None,
) -> TokenTransaction:
    """Add tokens to a Subscriber's account."""
    if amount <= 0:
        raise TokenServiceError("Credit amount must be positive.")
    account = get_or_create_account(db, subscriber_id)
    tx = _record_transaction(
        db, account,
        tx_type=tx_type,
        amount=amount,
        description=description,
        created_by_user_id=created_by_user_id,
    )
    if tx_type == "purchase":
        account.TotalPurchased += amount
    return tx


def debit(
    db: Session,
    subscriber_id: int,
    amount: Decimal,
    *,
    description: str,
    reference_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    bypass_balance_check: bool = False,
    source: str = "app",
) -> TokenTransaction:
    if amount <= 0:
        raise TokenServiceError("Debit amount must be positive.")

    # Trial per-service cap check (non-trial subscribers: instant pass)
    from app.services.trial_service import check_trial_service_limit
    trial_error = check_trial_service_limit(db, subscriber_id, reference_type, amount)
    if trial_error:
        raise InsufficientTokensError(amount, Decimal("0"), message=trial_error)

    account = get_or_create_account(db, subscriber_id)
    if not bypass_balance_check and account.TokenBalance < amount:
        raise InsufficientTokensError(amount, account.TokenBalance)
    tx = _record_transaction(
        db, account,
        tx_type="debit",
        amount=-amount,
        description=description,
        reference_id=reference_id,
        reference_type=reference_type,
        created_by_user_id=created_by_user_id,
        source=source,
    )
    account.TotalConsumed += amount
    return tx


def refund(
    db: Session,
    subscriber_id: int,
    amount: Decimal,
    *,
    description: str,
    reference_id: Optional[int] = None,
    reference_type: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> TokenTransaction:
    """Partial or full refund of a prior debit."""
    return credit(
        db, subscriber_id, amount,
        description=description,
        tx_type="refund",
        created_by_user_id=created_by_user_id,
    )


def provision_trial_tokens(
    db: Session,
    subscriber_id: int,
    plan_code: str,
    *,
    created_by_user_id: Optional[int] = None,
) -> Optional[TokenTransaction]:
    """
    Credit trial tokens when a new Trial-plan Subscriber is created.
    No-op if the plan has no TrialTokens value or the account already has tokens.
    """
    plan = db.get(SubscriptionPlanConfig, plan_code)
    if plan is None or not plan.TrialTokens:
        return None
    account = get_or_create_account(db, subscriber_id)
    if account.TotalPurchased > 0 or account.TokenBalance > 0:
        return None  # already has tokens — don't double-credit
    return credit(
        db, subscriber_id, Decimal(str(plan.TrialTokens)),
        description=f"Trial token allocation ({plan.DisplayName} plan)",
        tx_type="credit",
        created_by_user_id=created_by_user_id,
    )


# ---------------------------------------------------------------------------
# Monthly credit job
# ---------------------------------------------------------------------------

def credit_monthly_allocations(
    db: Session,
    *,
    created_by_user_id: Optional[int] = None,
) -> list[int]:
    """
    Credit monthly token allocations for Enterprise and Premium subscribers.
    Run on the 1st of each month. Returns list of SubscriberIDs credited.
    """
    from app.models.subscriber import Subscriber

    credited = []
    active_subs = (
        db.query(Subscriber)
        .join(SubscriptionPlanConfig,
              SubscriptionPlanConfig.PlanCode == Subscriber.SubscriptionPlan)
        .filter(
            Subscriber.SubscriberStatus.in_(["active", "expired_grace"]),
            SubscriptionPlanConfig.TokensPerMonth.isnot(None),
            SubscriptionPlanConfig.ContractMonths > 0,
        )
        .all()
    )
    for sub in active_subs:
        plan = db.get(SubscriptionPlanConfig, sub.SubscriptionPlan)
        if plan and plan.TokensPerMonth:
            credit(
                db, sub.SubscriberID, Decimal(str(plan.TokensPerMonth)),
                description=f"Monthly token allocation — {plan.DisplayName}",
                tx_type="credit",
                created_by_user_id=created_by_user_id,
            )
            credited.append(sub.SubscriberID)
    return credited


# ---------------------------------------------------------------------------
# Per-service rate multipliers
# ---------------------------------------------------------------------------

DEFAULT_SERVICE_RATES: dict[str, Decimal] = {
    "commercial":    Decimal("1.00"),   # baseline — 1 token per station-hour
    "song":          Decimal("0.50"),   # 50 % of baseline
    "word":          Decimal("0.75"),   # 75 %
    "transcription": Decimal("1.50"),   # 150 % — most compute-intensive
    "spectrum":      Decimal("0.25"),   # lightest service
}


def get_effective_rate(
    db: Session,
    subscriber_id: int,
    service_type: str,
) -> Decimal:
    """Return the rate multiplier (tokens per station-hour) for a subscriber + service.

    Checks SubscriberServiceRate for a per-subscriber override; falls back to
    DEFAULT_SERVICE_RATES, then 1.00 for unknown service types.
    Wrapped in a broad except so a missing table during the migration window
    never blocks a registration.
    """
    try:
        from app.models.billing import SubscriberServiceRate
        row = (
            db.query(SubscriberServiceRate)
            .filter(
                SubscriberServiceRate.SubscriberID == subscriber_id,
                SubscriberServiceRate.ServiceType == service_type,
            )
            .one_or_none()
        )
        if row is not None:
            return row.RatePerHour
    except Exception:
        pass
    return DEFAULT_SERVICE_RATES.get(service_type, Decimal("1.00"))


def get_effective_registration_fee(
    db: Session,
    subscriber_id: int,
    service_type: str,
) -> Optional[Decimal]:
    """Return the flat per-registration fee (tokens) for a subscriber + service, or None.

    None means no flat fee applies. A value of Decimal("0") is also valid (explicitly waived).
    Currently used for commercial spots; the field is available for any service type.
    """
    try:
        from app.models.billing import SubscriberServiceRate
        row = (
            db.query(SubscriberServiceRate)
            .filter(
                SubscriberServiceRate.SubscriberID == subscriber_id,
                SubscriberServiceRate.ServiceType == service_type,
            )
            .one_or_none()
        )
        if row is not None and row.RegistrationFee is not None:
            return row.RegistrationFee
    except Exception:
        pass
    return None


def calculate_cost_for_service(
    db: Session,
    subscriber_id: int,
    service_type: str,
    station_count: int,
    start_date: date,
    end_date: Optional[date],
) -> Decimal:
    """calculate_cost() × the effective per-service rate for this subscriber.

    Use this at every registration/subscription call site that has DB access.
    billing_service.py previews and bulk-upload validation (no subscriber
    context) should continue to call calculate_cost() directly.
    """
    base = calculate_cost(station_count, start_date, end_date)
    rate = get_effective_rate(db, subscriber_id, service_type)
    return (base * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_transactions(
    db: Session,
    subscriber_id: int,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TokenTransaction], int]:
    q = (
        db.query(TokenTransaction)
        .filter(TokenTransaction.SubscriberID == subscriber_id)
        .order_by(TokenTransaction.CreatedAt.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total
