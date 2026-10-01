"""
Subscriber management service (v0.3).

Replaces tenant_service. Single Subscriber table, seven types. Internal admins
use this to create/edit Subscribers; non-internal users do not reach these
operations.

Constraint-aware: status values + types match the DB CHECK constraints exactly;
plan dropdown sourced from SubscriptionPlanConfig.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.station import Station
from app.models.subscriber import (
    BILLING_MODES,
    SUBSCRIBER_STATUSES,
    SUBSCRIBER_TYPES,
    Subscriber,
)
from app.models.subscription_plan import SubscriptionPlanConfig
from app.utils.slug import slugify


class SubscriberError(Exception):
    """Raised when a Subscriber create/update request is invalid."""


def active_plans(db: Session) -> list[SubscriptionPlanConfig]:
    """Plans that can be assigned to a Subscriber (for form dropdowns)."""
    return (
        db.query(SubscriptionPlanConfig)
        .filter(SubscriptionPlanConfig.IsActive == True)  # noqa: E712 -- = 1 on SQL Server
        .order_by(SubscriptionPlanConfig.DisplayName)
        .all()
    )


def _valid_plan(db: Session, plan_code: str) -> None:
    if db.get(SubscriptionPlanConfig, plan_code) is None:
        raise SubscriberError(f"Unknown subscription plan: {plan_code!r}")


def _unique_slug(db: Session, base: str, exclude_id: Optional[int] = None) -> str:
    if not base:
        base = "subscriber"
    candidate = base
    n = 1
    while True:
        existing = (
            db.query(Subscriber).filter(Subscriber.Slug == candidate).one_or_none()
        )
        if existing is None:
            return candidate
        if exclude_id is not None and existing.SubscriberID == exclude_id:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def stations_without_subscriber(db: Session) -> list[Station]:
    """
    Stations not yet bound to a Radio Station-type Subscriber. Used by the
    'new Subscriber of type Radio Station' form to populate the station picker.
    """
    taken = (
        db.query(Subscriber.StationID)
        .filter(Subscriber.StationID.isnot(None))
        .subquery()
    )
    return (
        db.query(Station)
        .filter(Station.StationID.notin_(db.query(taken.c.StationID)))
        .order_by(Station.StationName)
        .all()
    )


def create_subscriber(
    db: Session,
    *,
    name: str,
    subscriber_type: str,
    plan: str,
    status: str = "pending",
    company_name: Optional[str] = None,
    other_description: Optional[str] = None,
    station_id: Optional[int] = None,
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
) -> Subscriber:
    name = (name or "").strip()
    if not name:
        raise SubscriberError("A name is required.")
    if contact_email:
        from app.utils.email_validator import validate_email, EmailValidationError
        try:
            contact_email = validate_email(contact_email)
        except EmailValidationError as e:
            raise SubscriberError(str(e)) from e
    if contact_phone:
        from app.utils.email_validator import validate_phone, PhoneValidationError
        try:
            contact_phone = validate_phone(contact_phone) or None
        except PhoneValidationError as e:
            raise SubscriberError(str(e)) from e
    if subscriber_type not in SUBSCRIBER_TYPES:
        raise SubscriberError(f"Invalid Subscriber type: {subscriber_type!r}")
    if status not in SUBSCRIBER_STATUSES:
        raise SubscriberError(f"Invalid status: {status!r}")
    _valid_plan(db, plan)

    other_description = (other_description or "").strip() or None
    if subscriber_type == "Other" and not other_description:
        raise SubscriberError("A brief description is required for type 'Other'.")
    if subscriber_type != "Other" and other_description:
        raise SubscriberError("OtherDescription only applies to type 'Other'.")

    if subscriber_type == "Radio Station":
        if station_id is None:
            raise SubscriberError("A station must be selected for Radio Station type.")
        if db.get(Station, station_id) is None:
            raise SubscriberError("Selected station does not exist.")
        dupe = (
            db.query(Subscriber)
            .filter(Subscriber.StationID == station_id)
            .one_or_none()
        )
        if dupe is not None:
            raise SubscriberError(
                f"That station already has a Subscriber ({dupe.Name})."
            )
    else:
        if station_id is not None:
            raise SubscriberError("StationID only applies to Radio Station type.")

    subscriber = Subscriber(
        Name=name,
        CompanyName=(company_name.strip() if company_name else None),
        Slug=_unique_slug(db, slugify(name)),
        SubscriberType=subscriber_type,
        OtherDescription=other_description,
        SubscriptionPlan=plan,
        SubscriberStatus=status,
        StationID=station_id,
        ContactEmail=(contact_email or None),
        ContactPhone=(contact_phone or None),
    )
    db.add(subscriber)
    db.flush()
    return subscriber


def update_subscriber(
    db: Session,
    subscriber: Subscriber,
    *,
    name: str,
    plan: str,
    status: str,
    company_name: Optional[str] = None,
    other_description: Optional[str] = None,
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
    billing_mode: str = "prepaid",
) -> Subscriber:
    """Update mutable fields. Type and StationID are NOT changed once set."""
    name = (name or "").strip()
    if not name:
        raise SubscriberError("A name is required.")
    if status not in SUBSCRIBER_STATUSES:
        raise SubscriberError(f"Invalid status: {status!r}")
    if billing_mode not in BILLING_MODES:
        raise SubscriberError(f"Invalid billing mode: {billing_mode!r}")
    _valid_plan(db, plan)

    other_description = (other_description or "").strip() or None
    if subscriber.SubscriberType == "Other" and not other_description:
        raise SubscriberError("A brief description is required for type 'Other'.")
    if subscriber.SubscriberType != "Other" and other_description:
        raise SubscriberError("OtherDescription only applies to type 'Other'.")

    if name != subscriber.Name:
        subscriber.Slug = _unique_slug(
            db, slugify(name), exclude_id=subscriber.SubscriberID
        )
    subscriber.Name = name
    subscriber.CompanyName = (company_name.strip() if company_name else None)
    subscriber.SubscriptionPlan = plan
    subscriber.SubscriberStatus = status
    subscriber.OtherDescription = other_description
    subscriber.ContactEmail = (contact_email or None)
    if contact_phone:
        from app.utils.email_validator import validate_phone, PhoneValidationError
        try:
            contact_phone = validate_phone(contact_phone) or None
        except PhoneValidationError as e:
            raise SubscriberError(str(e)) from e
    subscriber.ContactPhone = (contact_phone or None)
    subscriber.BillingMode = billing_mode
    db.flush()
    return subscriber


def deactivate_subscriber(
    db: Session,
    subscriber: Subscriber,
    *,
    new_status: str = "archived",
) -> Subscriber:
    """
    Deactivate a Subscriber by setting their status to archived or cancelled.
    Does NOT delete the row — preserves audit trail and all associated data.
    Caller commits.
    """
    if new_status not in ("archived", "cancelled"):
        raise SubscriberError("Status must be 'archived' or 'cancelled'.")
    if subscriber.SubscriberStatus in ("archived", "cancelled"):
        raise SubscriberError(
            f"Subscriber is already {subscriber.SubscriberStatus}."
        )
    subscriber.SubscriberStatus = new_status
    db.flush()
    return subscriber
