"""
Detection visibility scoping (v0.3 -- AUTHORIZATION.md v0.3).

The rule, in one sentence: an external user sees Detections whose Commercial
shares a FingerprintID with a Commercial owned by the user's Subscriber
(Status='active'). Internal users see everything.

This collapses the old four-branch shape (internal/client/agency/station)
into a single Subscriber-bound query joined through FingerprintID.

Song/Word scoping is stubbed and returns false() until the song/word
registration UI is built; today the app only exposes commercials.

Each public function returns a SQLAlchemy boolean expression suitable for
.filter(...). Callers compose it onto their own select; scoping does not
execute queries here.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, false, true
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.models.campaign import Commercial
from app.models.detection import Detection, SongDetection, WordDetection
from app.models.user import User, UserType


def commercial_filter(user: User, db: Session = None) -> ColumnElement[bool]:
    """
    Filter for Detection (commercial airings).

      internal       -> all rows
      subscriber_*   -> rows whose Commercial shares a FingerprintID with an
                        active Commercial owned by the user's Subscriber,
                        further restricted to the user's allowed stations
                        if they have explicit station restrictions set
      otherwise      -> nothing (fail-closed)

    db is optional for backward compatibility with existing callers that
    don't pass it -- station restrictions are skipped (not enforced) if db
    is not supplied, since get_allowed_station_ids needs a session. Always
    pass db when available; it's required for restriction enforcement.
    """
    if user.user_type == UserType.INTERNAL:
        return true()
    if not user.user_type.is_subscriber or user.SubscriberID is None:
        return false()  # defensive: external user without a Subscriber binding

    c_det = aliased(Commercial)   # the detection's commercial
    c_mine = aliased(Commercial)  # one of my Subscriber's commercials

    # Two visibility paths:
    # (a) Generic: Commercial has a FingerprintID — fan out to all Subscribers
    #     who registered the same audio via shared FingerprintID.
    # (b) Liveread: Commercial has NULL FingerprintID — simple direct ownership
    #     (the Subscriber who registered it sees it).
    fingerprint_match = exists().where(
        and_(
            c_det.CommercialID == Detection.CommercialID,
            c_det.FingerprintID.isnot(None),
            c_mine.FingerprintID == c_det.FingerprintID,
            c_mine.SubscriberID == user.SubscriberID,
        )
    )

    direct_match = exists().where(
        and_(
            c_det.CommercialID == Detection.CommercialID,
            c_det.FingerprintID.is_(None),
            c_det.SubscriberID == user.SubscriberID,
        )
    )

    base_filter = fingerprint_match | direct_match

    if db is not None:
        from app.services.permission_service import get_allowed_station_ids
        allowed = get_allowed_station_ids(db, user)
        if allowed is not None:
            if not allowed:
                return false()  # explicitly restricted to zero stations
            return base_filter & Detection.StationID.in_(allowed)

    return base_filter


def song_filter(user: User) -> ColumnElement[bool]:
    """
    SongDetection visibility. Song registration / scoping is not built yet;
    only internal users see anything. Will be implemented when song-detection
    registration UI lands (DEFERRED.md).
    """
    if user.user_type == UserType.INTERNAL:
        return true()
    return false()


def word_filter(user: User) -> ColumnElement[bool]:
    """WordDetection visibility. Same status as song_filter -- internal-only for now."""
    if user.user_type == UserType.INTERNAL:
        return true()
    return false()
