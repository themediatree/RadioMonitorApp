"""
Campaign + commercial registration (v0.3 R3 -- full implementation).

Flow on submit:
  1. Resolve + authorize the target Subscriber.
  2. Validate T&C confirmation (required checkbox).
  3. Validate dates and Tape ID.
  4. Handle the upload: generic -> convert to mp3; liveread -> keep as .txt.
  5. Fingerprint the mp3; check for existing FingerprintID (dedup).
  6. Create/locate Campaign; create Commercial; link to campaign + stations.
     - new fingerprint  -> stage file + write to audio_archive
     - existing fingerprint -> follower row only, no new file staged
  7. COMMIT (pipeline can resolve file to row by name).
  8. Fan out the file into staging (if new fingerprint).
  9. Audit log including T&C version + hash.
"""

import os
import tempfile
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.legal import TERMS_TEXT, TERMS_TEXT_HASH, TERMS_VERSION
from app.models.campaign import (
    Campaign, CampaignCommercial, CampaignStation, Commercial,
)
from app.models.station import Station
from app.models.subscriber import Subscriber
from app.models.user import User, UserType
from app.services.station_picker_service import (
    get_tbfp_stations_for_picker,
    resolve_tbfp_ids_to_station_ids,
)
from app.services import audit_service, registration_service
from app.services.registration_service import (
    ALLOWED_AUDIO_EXT, ALLOWED_TEXT_EXT, RegistrationError,
)
from app.templating import templates
from app.utils.audio import AudioError, convert_to_mp3

router = APIRouter(tags=["registration"])


def _ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _authorized_subscribers(db: Session, user: User) -> list[Subscriber]:
    if user.user_type == UserType.INTERNAL:
        return (
            db.query(Subscriber)
            .filter(Subscriber.SubscriberStatus == "active")
            .order_by(Subscriber.Name)
            .all()
        )
    if user.user_type.is_subscriber and user.SubscriberID is not None:
        s = db.get(Subscriber, user.SubscriberID)
        return [s] if s and s.SubscriberStatus == "active" else []
    return []


def _can_register_for(db: Session, user: User, subscriber_id: int) -> bool:
    return any(s.SubscriberID == subscriber_id
               for s in _authorized_subscribers(db, user))


def _active_stations(db: Session, user=None) -> list[Station]:
    """
    Active stations, respecting the user's station restrictions if any.
    user is optional for backward compatibility with any caller that
    doesn't have it handy -- always pass it when available so restrictions
    are actually enforced.
    """
    if user is not None:
        from app.services.permission_service import visible_stations
        return visible_stations(db, user)
    return (
        db.query(Station)
        .filter(Station.IsActive == True)  # noqa: E712
        .filter(Station.StationID != 6)
        .order_by(Station.StationName)
        .all()
    )


@router.get("/register-commercial", response_class=HTMLResponse)
def register_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.services.token_service import get_balance
    from app.services.billing_service import get_rate, get_exchange_rate
    from app.deps import get_currency
    from decimal import Decimal
    balance = get_balance(db, user.SubscriberID) if user.SubscriberID else Decimal("0")
    from app.models.subscriber import Subscriber
    subscriber = db.get(Subscriber, user.SubscriberID) if user.SubscriberID else None
    plan_code = subscriber.SubscriptionPlan if subscriber else "standard"
    return templates.TemplateResponse(
        request=request,
        name="app/register_commercial.html",
        context={
            "user": user,
            "subscribers": _authorized_subscribers(db, user),
            "stations": get_tbfp_stations_for_picker(db),
            "terms_text": TERMS_TEXT,
            "error": None,
            "form": {},
            **right_panel_context(user, db, request),
        },
    )


@router.post("/register-commercial")
async def register_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    campaign_name: Annotated[str, Form()],
    commercial_type: Annotated[str, Form()],
    tape_id: Annotated[str, Form()],
    terms_agreed: Annotated[str, Form()] = "",
    brand: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
    subscriber_id: Annotated[str, Form()] = "",
    station_ids: Annotated[list[str], Form()] = None,
    upload: Annotated[UploadFile, File()] = None,
):
    from datetime import date

    # FastAPI's list[str] Form() misses repeated checkbox values in multipart.
    # Reading raw_form first then using upload from the same parsed multipart
    # is safe — FastAPI buffers the entire body before dispatching.
    raw_form = await request.form()
    station_ids = list(raw_form.getlist("station_ids"))
    # Re-get the upload from raw_form so we're reading from the buffered object.
    upload = raw_form.get("upload") or upload

    def rerender(error: str):
        from app.services.token_service import get_balance, is_postpaid as _is_postpaid
        balance = get_balance(db, user.SubscriberID) if user.SubscriberID else 0
        return templates.TemplateResponse(
            request=request,
            name="app/register_commercial.html",
            context={
                "user": user,
                "subscribers": _authorized_subscribers(db, user),
                "stations": get_tbfp_stations_for_picker(db),
                "terms_text": TERMS_TEXT,
                "token_balance": float(balance),
                "is_postpaid": _is_postpaid(db, user.SubscriberID) if user.SubscriberID else False,
                "error": error,
                "form": {
                    "campaign_name": campaign_name,
                    "commercial_type": commercial_type,
                    "tape_id": tape_id,
                    "brand": brand,
                    "start_date": start_date,
                    "end_date": end_date,
                    "subscriber_id": subscriber_id,
                },
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # --- 1. Resolve Subscriber ---
    if user.user_type == UserType.INTERNAL:
        if not subscriber_id.isdigit():
            return rerender("Please select a Subscriber.")
        target_subscriber_id = int(subscriber_id)
    elif user.user_type.is_subscriber:
        if user.SubscriberID is None:
            return rerender("Your user is not bound to a Subscriber.")
        target_subscriber_id = user.SubscriberID
    else:
        return rerender("You are not permitted to register commercials.")

    if not _can_register_for(db, user, target_subscriber_id):
        return rerender("You are not authorised to register for that Subscriber.")
    sub = db.get(Subscriber, target_subscriber_id)
    if sub is None or sub.SubscriberStatus != "active":
        return rerender("That Subscriber does not have an active status.")

    # --- 2. T&C confirmation ---
    if terms_agreed not in ("1", "on", "yes", "true"):
        return rerender(
            "You must confirm that you are the lawful owner or authorised "
            "representative before registering this commercial."
        )

    # --- 3. Validate type, dates, tape id ---
    if commercial_type not in registration_service.VALID_COMMERCIAL_TYPES:
        return rerender("Choose a commercial type.")
    try:
        sd = date.fromisoformat(start_date) if start_date else None
        if not end_date:
            return rerender("End date is required.")
            ed = None
        ed = date.fromisoformat(end_date)
    except ValueError:
        return rerender("Dates must be valid (YYYY-MM-DD).")
    if sd is None:
        return rerender("A start date is required.")
    try:
        registration_service.validate_campaign_dates(sd, ed)
        clean_tape_id = registration_service.sanitize_tape_id(tape_id)
    except RegistrationError as e:
        return rerender(str(e))

    tbfp_ids = [int(s) for s in (station_ids or []) if s.isdigit()]
    sids = resolve_tbfp_ids_to_station_ids(db, tbfp_ids)
    if not sids:
        return rerender("Select at least one station.")
    try:
        station_names = registration_service.resolve_station_names(db, sids)
    except RegistrationError as e:
        return rerender(str(e))

    # --- 4. Handle upload ---
    if upload is None or not upload.filename:
        return rerender("Please upload the commercial file.")
    _stem, ext = os.path.splitext(upload.filename)
    ext = ext.lower()
    is_generic = commercial_type == "generic"
    if is_generic and ext not in ALLOWED_AUDIO_EXT:
        return rerender(
            f"Generic commercials must be audio "
            f"({', '.join(sorted(ALLOWED_AUDIO_EXT))})."
        )
    if not is_generic and ext not in ALLOWED_TEXT_EXT:
        return rerender("Liveread commercials must be a .txt script.")

    commercial_name = f"{target_subscriber_id}_{clean_tape_id}"
    tmp_dir = tempfile.mkdtemp(prefix="rmupload_")
    try:
        raw_path = os.path.join(tmp_dir, f"upload{ext}")
        contents = await upload.read()
        with open(raw_path, "wb") as f:
            f.write(contents)

        if is_generic:
            final_ext = ".mp3"
            final_src = os.path.join(tmp_dir, "final.mp3")
            try:
                convert_to_mp3(raw_path, final_src)
            except AudioError as e:
                return rerender(str(e))
        else:
            final_ext = ".txt"
            final_src = raw_path

        # --- 5. Fingerprint + dedup (generic only) ---
        fingerprint_id: Optional[str] = None
        fingerprint_exists_in_system = False   # is this FingerprintID known at all?
        subscriber_already_has_this_fp = False  # does THIS Subscriber already have it?

        if is_generic:
            try:
                from app.utils.fingerprint import compute_fingerprint_id
                fingerprint_id, _ = compute_fingerprint_id(final_src)
            except RuntimeError as e:
                return rerender(f"Audio fingerprinting failed: {e}")

            # Check system-wide: does any active/pending row share this fingerprint?
            existing_system = (
                db.query(Commercial)
                .filter(
                    Commercial.FingerprintID == fingerprint_id,
                    Commercial.Status.in_(["active", "pending"]),
                )
                .first()
            )
            if existing_system is not None:
                fingerprint_exists_in_system = True

            # Check per-Subscriber: does THIS Subscriber already have an active
            # registration for this fingerprint? If yes, it's a true duplicate
            # and we shouldn't stage again.
            existing_for_subscriber = (
                db.query(Commercial)
                .filter(
                    Commercial.FingerprintID == fingerprint_id,
                    Commercial.SubscriberID == target_subscriber_id,
                    Commercial.Status.in_(["active", "pending"]),
                )
                .first()
            )
            if existing_for_subscriber is not None:
                subscriber_already_has_this_fp = True

        # Should we stage files? Yes if:
        #   - it's liveread (always stages -- no fingerprint/dedup concept,
        #     every liveread registration is staged independently), OR
        #   - it's a new fingerprint in the system (file-owner row), OR
        #   - the fingerprint exists but this Subscriber doesn't have it yet
        #     (follower row that still needs its own files staged per station)
        # No if this Subscriber already has an active generic registration
        # for this audio (true duplicate).
        should_stage = (not is_generic) or (is_generic and not subscriber_already_has_this_fp)

        # --- 6. DB rows ---
        try:
            # Per-Subscriber TapeID uniqueness check (friendly message).
            dupe = (
                db.query(Commercial)
                .filter(
                    Commercial.SubscriberID == target_subscriber_id,
                    Commercial.DisplayTapeID == clean_tape_id,
                    Commercial.Status != "withdrawn",
                )
                .one_or_none()
            )
            if dupe is not None:
                return rerender(
                    f"You already have a commercial with Tape ID {clean_tape_id!r}."
                )

            campaign, _created = registration_service.get_or_create_campaign(
                db, subscriber_id=target_subscriber_id,
                name=campaign_name, start_date=sd, end_date=ed,
            )
            commercial = Commercial(
                CommercialName=commercial_name,
                DisplayTapeID=clean_tape_id,
                SubscriberID=target_subscriber_id,
                FingerprintID=fingerprint_id,
                Brand=(brand.strip() or None),
                CommercialType=commercial_type,
                Status="pending",
                IsActive=True,
                CreatedByUserID=user.UserID,
            )
            db.add(commercial)
            db.flush()
            db.add(CampaignCommercial(
                CampaignID=campaign.CampaignID,
                CommercialID=commercial.CommercialID,
            ))
            existing_cs = {
                cs.StationID for cs in
                db.query(CampaignStation)
                .filter(CampaignStation.CampaignID == campaign.CampaignID)
                .all()
            }
            for sid in sids:
                if sid not in existing_cs:
                    db.add(CampaignStation(
                        CampaignID=campaign.CampaignID, StationID=sid
                    ))
            db.flush()

            # Save schedule windows per station if provided
            from app.services.schedule_service import schedules_from_form, save_campaign_station_schedules
            raw_form2 = await request.form()
            schedule_dicts = schedules_from_form(raw_form2)
            if schedule_dicts:
                for sid in sids:
                    save_campaign_station_schedules(db, campaign.CampaignID, sid, schedule_dicts)

            # --- Generic transcription job (deduped by FingerprintID) ---
            # Only one job per FingerprintID, ever -- the transcript is
            # reused by every Subscriber who shares this audio. Skip
            # entirely for liveread (no FingerprintID) or if a job already
            # exists (covers both "already transcribed" and "already
            # queued" cases without a second DB round-trip).
            if is_generic and fingerprint_id:
                from app.models.detection import GenericTranscriptionJob
                existing_job = (
                    db.query(GenericTranscriptionJob)
                    .filter(GenericTranscriptionJob.FingerprintID == fingerprint_id)
                    .one_or_none()
                )
                if existing_job is None:
                    from datetime import datetime as _dt
                    from app.utils.fingerprint import archive_path as _archive_path
                    db.add(GenericTranscriptionJob(
                        FingerprintID=fingerprint_id,
                        CommercialID=commercial.CommercialID,
                        AudioPath=_archive_path(settings.audio_archive_root, fingerprint_id),
                        Status="pending",
                        CreatedAt=_dt.now(),
                    ))
        except RegistrationError as e:
            db.rollback()
            return rerender(str(e))

        # --- 6b. Token balance check + debit ---
        from app.services.token_service import (
            calculate_cost, calculate_cost_for_service, debit, get_balance, InsufficientTokensError
        )
        from decimal import Decimal
        if schedule_dicts and ed:
            from app.services.schedule_service import calculate_scheduled_hours
            all_days_mode = any(s['day'] == -1 for s in schedule_dicts)
            single_day = (sd or date.today()) == ed
            if all_days_mode or single_day:
                total_mins = sum(
                    (s['to'].hour * 60 + s['to'].minute) - (s['from'].hour * 60 + s['from'].minute)
                    for s in schedule_dicts
                )
                num_days = (ed - (sd or date.today())).days + 1
                scheduled_hours = (total_mins / 60.0) * (num_days if all_days_mode else 1)
            else:
                sched_objs = [type('S', (), {'DayOfWeek': s['day'], 'TimeFrom': s['from'], 'TimeTo': s['to']})() for s in schedule_dicts]
                scheduled_hours = calculate_scheduled_hours(sched_objs, sd or date.today(), ed, len(sids))
            token_cost = Decimal(str(round(max(scheduled_hours, 1/60), 4)))
        else:
            token_cost = calculate_cost_for_service(db, target_subscriber_id, "commercial", len(sids), sd or date.today(), ed)
        balance = get_balance(db, target_subscriber_id)
        if balance < token_cost:
            db.rollback()
            return rerender(
                f"Insufficient credits. This registration requires "
                f"{token_cost:.2f} credits "
                f"({len(sids)} station{'s' if len(sids)!=1 else ''} × "
                f"{int(token_cost // (len(sids) * 24))} days). "
                f"Your balance is {balance:.2f} credits."
            )

        # --- 7. COMMIT before staging ---
        db.commit()

        # Activate stations immediately if campaign starts today or in the past
        try:
            from app.services.station_scheduler import activate_station_if_needed
            if sd is None or sd <= date.today():
                for sid in sids:
                    activate_station_if_needed(db, sid)
                if sids:
                    db.commit()
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning(
                "Station activation check failed after registration: %s", _e
            )

        # Activate commercial immediately if start date is today or in the past
        try:
            from app.services.registration_service import activate_due_commercials
            if sd is None or sd <= date.today():
                activated = activate_due_commercials(db)
                if activated:
                    db.commit()
                    import logging
                    logging.getLogger(__name__).info(
                        "Commercial(s) activated immediately on registration: %s", activated
                    )
        except Exception as _e:
            import logging
            logging.getLogger(__name__).warning(
                "Commercial activation check failed after registration: %s", _e
            )

        # Debit after commit so the commercial row exists for the reference.
        try:
            from decimal import Decimal
            from app.services.token_service import get_effective_registration_fee, is_postpaid
            _postpaid = is_postpaid(db, target_subscriber_id)
            debit(
                db, target_subscriber_id, token_cost,
                description=(
                    f"Commercial registration: {clean_tape_id} — "
                    f"{len(sids)} station{'s' if len(sids)!=1 else ''} × "
                    f"{int(token_cost // (len(sids) * 24))} days"
                ),
                reference_id=commercial.CommercialID,
                reference_type="commercial",
                created_by_user_id=user.UserID,
                bypass_balance_check=_postpaid,
            )
            # Flat per-registration fee (if configured for this subscriber)
            reg_fee = get_effective_registration_fee(db, target_subscriber_id, "commercial")
            if reg_fee and reg_fee > 0:
                debit(
                    db, target_subscriber_id, reg_fee,
                    description=f"Commercial registration fee: {clean_tape_id}",
                    reference_id=commercial.CommercialID,
                    reference_type="commercial",
                    created_by_user_id=user.UserID,
                    bypass_balance_check=_postpaid,
                )
            db.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "Token debit failed after commercial registration %s: %s",
                commercial.CommercialID, e
            )

        # --- 8. Stage file ---
        if should_stage:
            # Write to audio archive on first-ever registration of this audio.
            if not fingerprint_exists_in_system and fingerprint_id:
                try:
                    from app.utils.fingerprint import write_to_archive
                    write_to_archive(final_src, settings.audio_archive_root, fingerprint_id)
                except RuntimeError as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Audio archive write failed for %s: %s", fingerprint_id, e
                    )
            elif fingerprint_exists_in_system and fingerprint_id:
                # Fingerprint known system-wide but new for this Subscriber.
                # Use the archived copy as source if the uploaded file is gone.
                from app.utils.fingerprint import archive_path as _archive_path
                arch = _archive_path(settings.audio_archive_root, fingerprint_id)
                if os.path.exists(arch) and not os.path.exists(final_src):
                    final_src = arch

            try:
                registration_service.fan_out_to_staging(
                    station_names=station_names,
                    category=commercial_type,
                    tape_id=commercial_name,
                    source_path=final_src,
                    ext=final_ext,
                )
            except RegistrationError as e:
                commercial.Status = "withdrawn"
                db.commit()
                return rerender(
                    f"Saved the record but staging the file failed: {e}. "
                    "The commercial was marked withdrawn; please retry."
                )

        # --- 9. Audit log ---
        audit_service.record(
            db, action="commercial_registered",
            actor_user_id=user.UserID,
            target_subscriber_id=target_subscriber_id,
            details=(
                f"tape_id={clean_tape_id} type={commercial_type} "
                f"stations={station_names} campaign={campaign.Name} "
                f"fingerprint_id={fingerprint_id or 'n/a'} "
                f"fingerprint_new_in_system={not fingerprint_exists_in_system} "
                f"staged={should_stage} "
                f"terms_version={TERMS_VERSION} "
                f"terms_hash={TERMS_TEXT_HASH}"
            ),
            ip_address=_ip(request),
        )

        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        try:
            import shutil as _sh
            _sh.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
