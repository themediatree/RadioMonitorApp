"""
Audit log service.

Single entry point: record(). Writes to the AuditLog table, whose real
schema is generic: (UserID, Action, ResourceType, ResourceID, Metadata,
IPAddress, UserAgent, ImpersonationSessionID, CreatedAt).

The callers pass semantic fields (actor, target user/subscriber, details).
We map them onto the generic columns:
    UserID        <- actor_user_id
    ResourceType  <- derived from whichever target is set ('user'/'subscriber')
    ResourceID    <- the target id (as string)
    Metadata      <- a small JSON blob holding details + any extra targets

Never raises -- audit failures are logged but don't break the request.
"""

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def _derive_resource(
    target_user_id: Optional[int],
    target_subscriber_id: Optional[int],
) -> tuple[Optional[str], Optional[str]]:
    """Pick the primary resource type/id from whichever target is provided."""
    if target_user_id is not None:
        return "user", str(target_user_id)
    if target_subscriber_id is not None:
        return "subscriber", str(target_subscriber_id)
    return None, None


def record(
    db: Session,
    *,
    action: str,
    actor_user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    target_subscriber_id: Optional[int] = None,
    impersonation_session_id: Optional[int] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Write an audit row. Never raises; truncates oversized strings."""
    try:
        resource_type, resource_id = _derive_resource(
            target_user_id, target_subscriber_id
        )

        meta: dict = {}
        if details:
            meta["details"] = details
        if target_user_id is not None:
            meta["target_user_id"] = target_user_id
        if target_subscriber_id is not None:
            meta["target_subscriber_id"] = target_subscriber_id
        meta_json = json.dumps(meta) if meta else None

        entry = AuditLog(
            UserID=actor_user_id,
            ImpersonationSessionID=impersonation_session_id,
            Action=action[:100],
            ResourceType=resource_type,
            ResourceID=resource_id,
            IPAddress=(ip_address or None) and ip_address[:64],
            UserAgent=(user_agent or None) and user_agent[:512],
            Metadata_=(meta_json or None) and meta_json[:4000],
        )
        db.add(entry)
        db.commit()
    except Exception as e:
        logger.exception("Failed to write audit log row: action=%s err=%s", action, e)
        try:
            db.rollback()
        except Exception:
            pass
