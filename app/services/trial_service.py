"""Trial service — provisioning and per-service credit limit helpers.

Used by:
  - token_service.debit()  — enforce per-service caps before debiting
  - Internal admin routes  — provision / view / expire trial accounts
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.billing import TokenTransaction
from app.models.trial_config import SubscriberTrialConfig


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get_trial_config(db: Session, subscriber_id: int) -> Optional[SubscriberTrialConfig]:
    """Return active trial config for a subscriber, or None if not a trial user."""
    cfg = db.get(SubscriberTrialConfig, subscriber_id)
    if cfg is None:
        return None
    # Expired trial — still return the row so callers can show expiry message
    return cfg


def is_trial_subscriber(db: Session, subscriber_id: int) -> bool:
    return db.get(SubscriberTrialConfig, subscriber_id) is not None


def is_trial_active(db: Session, subscriber_id: int) -> bool:
    cfg = db.get(SubscriberTrialConfig, subscriber_id)
    return cfg is not None and cfg.is_active


# ---------------------------------------------------------------------------
# Per-service consumption
# ---------------------------------------------------------------------------

def get_service_consumed(
    db: Session,
    subscriber_id: int,
    reference_type: str,
) -> Decimal:
    """Sum of credits debited for a specific service type for this subscriber."""
    result = (
        db.query(func.sum(TokenTransaction.TokenAmount))
        .filter(
            TokenTransaction.SubscriberID == subscriber_id,
            TokenTransaction.Type == "debit",
            TokenTransaction.ReferenceType == reference_type,
        )
        .scalar()
    )
    return abs(result) if result else Decimal("0")


def get_all_service_consumed(
    db: Session,
    subscriber_id: int,
) -> dict[str, Decimal]:
    """Return dict of consumed credits per service type."""
    rows = (
        db.query(
            TokenTransaction.ReferenceType,
            func.sum(TokenTransaction.TokenAmount),
        )
        .filter(
            TokenTransaction.SubscriberID == subscriber_id,
            TokenTransaction.Type == "debit",
            TokenTransaction.ReferenceType.isnot(None),
        )
        .group_by(TokenTransaction.ReferenceType)
        .all()
    )
    return {ref_type: abs(total) for ref_type, total in rows}


def check_trial_service_limit(
    db: Session,
    subscriber_id: int,
    reference_type: Optional[str],
    amount: Decimal,
) -> Optional[str]:
    """
    Check if a trial debit would exceed the per-service cap.
    Returns an error message string if exceeded, or None if OK.
    Non-trial subscribers always return None.
    """
    cfg = db.get(SubscriberTrialConfig, subscriber_id)
    if cfg is None:
        return None  # not a trial subscriber — no check needed

    if not cfg.is_active:
        return "Your trial period has expired. Please contact NOCTIV to upgrade your account."

    cap = cfg.cap_for(reference_type)
    if cap is None:
        return None  # no cap for this service type

    consumed = get_service_consumed(db, subscriber_id, reference_type)
    if consumed + amount > cap:
        remaining = max(Decimal("0"), cap - consumed)
        service_label = {
            "commercial": "Commercial detection",
            "song": "Song monitoring",
            "word": "Keyword monitoring",
            "transcription": "Transcription",
            "spectrum": "Spectrum analysis",
        }.get(reference_type or "", reference_type or "this service")
        return (
            f"Trial limit reached for {service_label}. "
            f"You have {remaining:.2f} credits remaining for this service "
            f"({cap:.0f} credit trial allocation)."
        )
    return None


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

TRIAL_DURATION_DAYS = 7
TRIAL_TOTAL_CREDITS = Decimal("168")

DEFAULT_ALLOCATIONS = {
    "MaxCommercial":    Decimal("60"),
    "MaxSong":          Decimal("40"),
    "MaxWord":          Decimal("40"),
    "MaxTranscription": Decimal("28"),
}


def provision_trial(
    db: Session,
    subscriber_id: int,
    station_id: int,
    created_by_user_id: int,
    max_commercial:    Decimal = DEFAULT_ALLOCATIONS["MaxCommercial"],
    max_song:          Decimal = DEFAULT_ALLOCATIONS["MaxSong"],
    max_word:          Decimal = DEFAULT_ALLOCATIONS["MaxWord"],
    max_transcription: Decimal = DEFAULT_ALLOCATIONS["MaxTranscription"],
    duration_days: int = TRIAL_DURATION_DAYS,
) -> SubscriberTrialConfig:
    """Create or replace a trial config for a subscriber."""
    existing = db.get(SubscriberTrialConfig, subscriber_id)
    if existing:
        db.delete(existing)
        db.flush()

    cfg = SubscriberTrialConfig(
        SubscriberID=subscriber_id,
        TrialExpiresAt=datetime.utcnow() + timedelta(days=duration_days),
        StationID=station_id,
        MaxCommercial=max_commercial,
        MaxSong=max_song,
        MaxWord=max_word,
        MaxTranscription=max_transcription,
        CreatedByUserID=created_by_user_id,
    )
    db.add(cfg)
    db.flush()
    return cfg


def expire_trial(db: Session, subscriber_id: int) -> bool:
    """Immediately expire a trial account. Returns True if found."""
    cfg = db.get(SubscriberTrialConfig, subscriber_id)
    if cfg is None:
        return False
    cfg.TrialExpiresAt = datetime.utcnow() - timedelta(seconds=1)
    db.flush()
    return True
