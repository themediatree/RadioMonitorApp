"""
app/services/api_key_service.py

API key management: generation, hashing, verification, rate limiting,
and signed clip URL creation.

Keys are formatted as: noctiv_<32-char-random>
Only the hash is stored; the plaintext is shown once at generation time.
Rate limit: 100 requests per 60-second sliding window per key, in-memory.
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

import bcrypt
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey, SignedClipToken

KEY_PREFIX_STR = "noctiv_"
RATE_LIMIT = 100       # requests
RATE_WINDOW = 60       # seconds
CLIP_TOKEN_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# In-memory rate limiter (sliding window per key prefix)
# ---------------------------------------------------------------------------
_windows: dict[str, deque] = {}
_lock = Lock()


def _check_rate_limit(key_prefix: str) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    with _lock:
        if key_prefix not in _windows:
            _windows[key_prefix] = deque()
        w = _windows[key_prefix]
        cutoff = now - RATE_WINDOW
        while w and w[0] < cutoff:
            w.popleft()
        if len(w) >= RATE_LIMIT:
            return False
        w.append(now)
        return True


# ---------------------------------------------------------------------------
# Key generation and verification
# ---------------------------------------------------------------------------

def generate_key() -> tuple[str, str, str]:
    """
    Returns (plaintext, hash, prefix).
    plaintext is shown once and never stored.
    """
    raw = KEY_PREFIX_STR + secrets.token_urlsafe(32)
    hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=10)).decode()
    prefix = raw[:16]
    return raw, hashed, prefix


def create_api_key(
    db: Session,
    subscriber_id: int,
    created_by_user_id: int,
    label: Optional[str] = None,
) -> tuple[ApiKey, str]:
    """Creates a new key row. Returns (row, plaintext). Commit after calling."""
    plaintext, hashed, prefix = generate_key()
    # Deactivate any existing active key for this subscriber first
    db.query(ApiKey).filter(
        ApiKey.SubscriberID == subscriber_id,
        ApiKey.IsActive == True,  # noqa: E712
    ).update({"IsActive": False})

    key = ApiKey(
        SubscriberID=subscriber_id,
        KeyHash=hashed,
        KeyPrefix=prefix,
        Label=label,
        IsActive=True,
        CreatedByUserID=created_by_user_id,
    )
    db.add(key)
    db.flush()
    return key, plaintext


def revoke_api_key(db: Session, subscriber_id: int) -> None:
    """Revokes all active keys for a subscriber. Commit after calling."""
    db.query(ApiKey).filter(
        ApiKey.SubscriberID == subscriber_id,
        ApiKey.IsActive == True,  # noqa: E712
    ).update({"IsActive": False})


def verify_api_key(db: Session, raw_key: str) -> Optional[ApiKey]:
    """
    Verifies an API key. Returns the ApiKey row if valid, None otherwise.
    Also enforces rate limiting and updates LastUsedAt.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX_STR):
        return None

    prefix = raw_key[:16]

    if not _check_rate_limit(prefix):
        return None  # rate limited -- caller raises 429

    # Find candidate rows by prefix (avoids full table bcrypt scan)
    candidates = (
        db.query(ApiKey)
        .filter(
            ApiKey.KeyPrefix == prefix,
            ApiKey.IsActive == True,  # noqa: E712
        )
        .all()
    )

    for key in candidates:
        if bcrypt.checkpw(raw_key.encode(), key.KeyHash.encode()):
            key.LastUsedAt = datetime.now()
            db.flush()
            return key

    return None


def get_active_key(db: Session, subscriber_id: int) -> Optional[ApiKey]:
    """Returns the active key row for display (prefix only, no plaintext)."""
    return (
        db.query(ApiKey)
        .filter(
            ApiKey.SubscriberID == subscriber_id,
            ApiKey.IsActive == True,  # noqa: E712
        )
        .first()
    )


# ---------------------------------------------------------------------------
# Signed clip tokens
# ---------------------------------------------------------------------------

POB_CLIP_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days for PoB certificates


def create_signed_clip_token(db: Session, clip_path: str) -> str:
    """Creates a short-lived signed token (1 hour) for API clip access."""
    token = secrets.token_urlsafe(32)
    db.add(SignedClipToken(
        Token=token,
        ClipPath=clip_path,
        ExpiresAt=datetime.now() + timedelta(seconds=CLIP_TOKEN_TTL),
    ))
    db.flush()
    return token


POB_CLIP_TOKEN_TTL = 60 * 60 * 24 * 30  # 30 days for PoB certificates


def create_pob_clip_token(db: Session, clip_path: str) -> str:
    """Creates a long-lived signed token (30 days) for use in PDF reports."""
    token = secrets.token_urlsafe(32)
    db.add(SignedClipToken(
        Token=token,
        ClipPath=clip_path,
        ExpiresAt=datetime.now() + timedelta(seconds=POB_CLIP_TOKEN_TTL),
    ))
    db.flush()
    return token



    """Creates a long-lived signed token (30 days) for use in Proof of Broadcast PDFs."""
    token = secrets.token_urlsafe(32)
    db.add(SignedClipToken(
        Token=token,
        ClipPath=clip_path,
        ExpiresAt=datetime.now() + timedelta(seconds=POB_CLIP_TOKEN_TTL),
    ))
    db.flush()
    return token



    """Creates a signed token for a clip. Returns the token string."""
    token = secrets.token_urlsafe(32)
    db.add(SignedClipToken(
        Token=token,
        ClipPath=clip_path,
        ExpiresAt=datetime.now() + timedelta(seconds=CLIP_TOKEN_TTL),
    ))
    db.flush()
    return token


def redeem_signed_clip_token(db: Session, token: str) -> Optional[str]:
    """
    Returns the clip path if the token is valid. Does NOT mark as used --
    clips can be played multiple times within the TTL window (range requests
    for audio need repeated access). Expired tokens return None.
    """
    row = db.query(SignedClipToken).filter(SignedClipToken.Token == token).first()
    if row is None or not row.is_valid:
        return None
    return row.ClipPath
