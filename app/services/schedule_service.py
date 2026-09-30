"""
app/services/schedule_service.py

Utilities for time-windowed subscription schedules.

Key functions:
  calculate_scheduled_hours()       — total booked hours for billing
  is_detection_in_schedule()        — True if detection falls within booked windows
  get_subscription_schedules()      — fetch SubscriptionSchedule rows
  get_campaign_station_schedules()  — fetch CampaignStationSchedule rows
  schedules_from_form()             — parse schedule windows from form
  save_schedules()                  — save SubscriptionSchedule rows
  save_campaign_station_schedules() — save CampaignStationSchedule rows
  extend_schedule_to_cover()        — extend windows to cover out-of-schedule detections
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---------------------------------------------------------------------------
# Billing — calculate total booked hours
# ---------------------------------------------------------------------------

def calculate_scheduled_hours(
    schedules: list,
    start_date: date,
    end_date: date,
    station_count: int = 1,
) -> float:
    """
    Calculate total booked hours for billing given schedule rows, date range,
    and station count. No schedules = full 24h/day (existing behaviour).

    Schedule is a recurring weekly template — each day-of-week window applies
    to ALL matching days in the date range, not just one specific date.
    """
    if not schedules:
        days = (end_date - start_date).days + 1
        return days * 24.0 * station_count

    hours_per_dow: dict[int, float] = {}
    for s in schedules:
        from_mins = s.TimeFrom.hour * 60 + s.TimeFrom.minute
        to_mins   = s.TimeTo.hour   * 60 + s.TimeTo.minute
        hours_per_dow[s.DayOfWeek] = hours_per_dow.get(s.DayOfWeek, 0.0) + (to_mins - from_mins) / 60.0

    total = 0.0
    current = start_date
    while current <= end_date:
        total += hours_per_dow.get(current.weekday(), 0.0)
        current += timedelta(days=1)

    return total * station_count


# ---------------------------------------------------------------------------
# Detection checking
# ---------------------------------------------------------------------------

def is_detection_in_schedule(
    schedules: list,
    detection_datetime: datetime,
) -> bool:
    """
    Returns True if the detection falls within any booked time window for its
    day of week. Returns True (not highlighted) if no schedules are set.

    Schedule is a recurring weekly template — DayOfWeek 0=Monday … 6=Sunday.
    """
    if not schedules:
        return True  # no schedule = always in-schedule

    if detection_datetime is None:
        return True

    dow = detection_datetime.weekday()
    det_time = detection_datetime.time().replace(second=0, microsecond=0)

    for s in schedules:
        if s.DayOfWeek == -1 or s.DayOfWeek == dow:
            if s.TimeFrom <= det_time < s.TimeTo:
                return True

    return False


# ---------------------------------------------------------------------------
# Fetch schedule rows
# ---------------------------------------------------------------------------

def get_subscription_schedules(db: Session, subscription_id: int) -> list:
    """Fetch SubscriptionSchedule rows ordered by day then time."""
    from app.models.subscription_schedule import SubscriptionSchedule
    return (
        db.query(SubscriptionSchedule)
        .filter(SubscriptionSchedule.SubscriptionID == subscription_id)
        .order_by(SubscriptionSchedule.DayOfWeek, SubscriptionSchedule.TimeFrom)
        .all()
    )


def get_campaign_station_schedules(db: Session, campaign_id: int, station_id: int) -> list:
    """Fetch CampaignStationSchedule rows for a CampaignStation."""
    from app.models.campaign_station_schedule import CampaignStationSchedule
    return (
        db.query(CampaignStationSchedule)
        .filter(
            CampaignStationSchedule.CampaignID == campaign_id,
            CampaignStationSchedule.StationID  == station_id,
        )
        .order_by(CampaignStationSchedule.DayOfWeek, CampaignStationSchedule.TimeFrom)
        .all()
    )


# ---------------------------------------------------------------------------
# Parse schedule from form submission
# ---------------------------------------------------------------------------

def schedules_from_form(form_data) -> list[dict]:
    """
    Parse schedule windows from a form submission.

    Expected repeating fields:
        schedule_day[]   = 0..6
        schedule_from[]  = "06:00"
        schedule_to[]    = "09:00"

    Returns list of dicts: [{"day": 0, "from": time(6,0), "to": time(9,0)}, ...]
    """
    days  = form_data.getlist("schedule_day")
    froms = form_data.getlist("schedule_from")
    tos   = form_data.getlist("schedule_to")

    result = []
    for d, f, t in zip(days, froms, tos):
        try:
            day   = int(d)
            tfrom = time.fromisoformat(f)
            tto   = time.fromisoformat(t)
            if not (-1 <= day <= 6):  # -1 = all days mode
                continue
            if tto <= tfrom:
                continue
            result.append({"day": day, "from": tfrom, "to": tto})
        except (ValueError, TypeError):
            continue

    return result


# ---------------------------------------------------------------------------
# Save schedule rows
# ---------------------------------------------------------------------------

def save_schedules(
    db: Session,
    subscription_id: int,
    schedule_dicts: list[dict],
) -> None:
    """Replace all SubscriptionSchedule rows for a subscription. Caller commits."""
    from app.models.subscription_schedule import SubscriptionSchedule

    db.query(SubscriptionSchedule).filter(
        SubscriptionSchedule.SubscriptionID == subscription_id
    ).delete()

    for s in schedule_dicts:
        db.add(SubscriptionSchedule(
            SubscriptionID=subscription_id,
            DayOfWeek=s["day"],
            TimeFrom=s["from"],
            TimeTo=s["to"],
        ))
    db.flush()


def save_campaign_station_schedules(
    db: Session,
    campaign_id: int,
    station_id: int,
    schedule_dicts: list[dict],
) -> None:
    """Replace all CampaignStationSchedule rows for a CampaignStation. Caller commits."""
    from app.models.campaign_station_schedule import CampaignStationSchedule

    db.query(CampaignStationSchedule).filter(
        CampaignStationSchedule.CampaignID == campaign_id,
        CampaignStationSchedule.StationID  == station_id,
    ).delete()

    for s in schedule_dicts:
        db.add(CampaignStationSchedule(
            CampaignID=campaign_id,
            StationID=station_id,
            DayOfWeek=s["day"],
            TimeFrom=s["from"],
            TimeTo=s["to"],
        ))
    db.flush()


# ---------------------------------------------------------------------------
# Format schedule for display
# ---------------------------------------------------------------------------

def format_schedule_summary(schedules: list) -> str:
    """Human-readable summary e.g. 'Mon 06:00–09:00 | Sat 09:00–12:00'"""
    if not schedules:
        return "Full day, every day"

    by_day: dict[int, list[str]] = {}
    for s in schedules:
        window = f"{s.TimeFrom:%H:%M}–{s.TimeTo:%H:%M}"
        by_day.setdefault(s.DayOfWeek, []).append(window)

    return " | ".join(
        f"{DAY_NAMES[dow][:3]} {', '.join(windows)}"
        for dow, windows in sorted(by_day.items())
    )


# ---------------------------------------------------------------------------
# Out-of-schedule detection context for dashboard right panel
# ---------------------------------------------------------------------------

def build_out_of_schedule_context(
    db,
    detections: list,
    subscription_schedule_map: dict,
    detection_id_attr: str,
    detection_time_attr: str,
    station_id_attr: str,
    label_fn,
    station_map: dict,
    update_url_fn,
    token_cost_fn,
) -> dict:
    """Builds context for the right panel out-of-schedule section."""
    schedule_flags = {}
    out_of_schedule = []

    for row in detections:
        det_id   = getattr(row, detection_id_attr)
        det_time = getattr(row, detection_time_attr, None)
        station_id = getattr(row, station_id_attr, None)
        schedules = subscription_schedule_map.get(
            getattr(row, 'SubscriptionID', None) or station_id, []
        )
        in_sched = is_detection_in_schedule(schedules, det_time) if (schedules and det_time) else True
        schedule_flags[det_id] = in_sched
        if not in_sched:
            out_of_schedule.append({
                "label":        label_fn(row),
                "station":      station_map.get(station_id, str(station_id)),
                "date":         det_time.strftime("%d %b %Y") if det_time else "—",
                "time":         det_time.strftime("%H:%M") if det_time else "—",
                "detection_id": det_id,
                "detection_time": det_time,
            })

    return {
        "schedule_flags":             schedule_flags,
        "out_of_schedule_count":      len(out_of_schedule),
        "out_of_schedule_items":      out_of_schedule,
        "out_of_schedule_update_url": update_url_fn(out_of_schedule) if out_of_schedule else None,
        "out_of_schedule_cost":       token_cost_fn(out_of_schedule) if out_of_schedule else "0",
    }


# ---------------------------------------------------------------------------
# UPDATE/INCLUDE — extend schedule to cover out-of-schedule detections
# ---------------------------------------------------------------------------

def extend_schedule_to_cover(
    db,
    subscription_id: int,
    out_of_schedule_detections: list,
    station_ids: list[int],
    end_date,
    rate_per_hour,
) -> dict:
    """
    Extends SubscriptionSchedule windows to cover out-of-schedule detections.
    Bills the difference at the same rate as the original registration.

    For each affected day-of-week:
      - Finds the latest detection time
      - Rounds up to next full hour
      - Extends the latest window for that day to that hour
      - Creates a window from 00:00 if none exists for that day

    Returns {"hours_added": float, "cost": Decimal, "days_extended": list}
    """
    from decimal import Decimal
    from app.models.subscription_schedule import SubscriptionSchedule

    today = datetime.now().date()

    # Group detections by day-of-week, find latest rounded-up time per dow
    latest_by_dow: dict[int, time] = {}
    for det in out_of_schedule_detections:
        det_time = det.get("detection_time") if isinstance(det, dict) else getattr(det, "detection_time", None)
        if not det_time:
            continue
        dow = det_time.weekday()
        next_hour = det_time.hour + 1 if (det_time.minute > 0 or det_time.second > 0) else det_time.hour
        if next_hour >= 24:
            next_hour = 23
        rounded = time(next_hour, 0, 0)
        if dow not in latest_by_dow or rounded > latest_by_dow[dow]:
            latest_by_dow[dow] = rounded

    if not latest_by_dow:
        return {"hours_added": 0, "cost": Decimal("0"), "days_extended": []}

    total_hours_added = 0.0
    days_extended = []

    for dow, new_end_time in latest_by_dow.items():
        existing = (
            db.query(SubscriptionSchedule)
            .filter(
                SubscriptionSchedule.SubscriptionID == subscription_id,
                SubscriptionSchedule.DayOfWeek == dow,
            )
            .order_by(SubscriptionSchedule.TimeTo.desc())
            .first()
        )

        if existing:
            old_end = existing.TimeTo
            if new_end_time <= old_end:
                continue
            hours_gained = (
                new_end_time.hour * 60 + new_end_time.minute -
                old_end.hour * 60 - old_end.minute
            ) / 60.0
            existing.TimeTo = new_end_time
        else:
            hours_gained = new_end_time.hour + new_end_time.minute / 60.0
            db.add(SubscriptionSchedule(
                SubscriptionID=subscription_id,
                DayOfWeek=dow,
                TimeFrom=time(0, 0, 0),
                TimeTo=new_end_time,
            ))

        # Count remaining occurrences of this dow in the subscription period
        remaining_days = 0
        if end_date:
            current = today
            while current <= end_date:
                if current.weekday() == dow:
                    remaining_days += 1
                current += timedelta(days=1)
        else:
            remaining_days = 4  # default 4 weeks if open-ended

        total_hours_added += hours_gained * remaining_days * len(station_ids)
        days_extended.append(f"{DAY_NAMES[dow]} → {new_end_time:%H:%M}")

    db.flush()

    cost = Decimal(str(round(total_hours_added * float(rate_per_hour), 4)))
    return {
        "hours_added":  total_hours_added,
        "cost":         cost,
        "days_extended": days_extended,
    }
