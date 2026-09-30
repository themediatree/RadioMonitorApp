"""
app/middleware/rate_limit.py

Simple in-memory rate limiter for the login endpoint.
Limits failed login attempts per IP address.

5 failed attempts within 10 minutes → 15-minute lockout.
Resets on successful login.

Uses a simple dict — adequate for a single-server deployment.
For multi-server, replace with Redis.
"""

import time
from collections import defaultdict
from threading import Lock
from typing import Dict, Tuple

_lock = Lock()
_attempts: Dict[str, list] = defaultdict(list)  # ip -> [timestamp, ...]
_lockouts: Dict[str, float] = {}                # ip -> lockout_until

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 600     # 10 minutes
LOCKOUT_SECONDS = 900    # 15 minutes


def _client_ip(request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return getattr(request.client, "host", "unknown")


def check_rate_limit(request) -> Tuple[bool, int]:
    """
    Returns (allowed: bool, retry_after_seconds: int).
    Call before processing a login attempt.
    """
    ip = _client_ip(request)
    now = time.time()

    with _lock:
        # Check lockout
        if ip in _lockouts:
            if now < _lockouts[ip]:
                retry = int(_lockouts[ip] - now)
                return False, retry
            else:
                del _lockouts[ip]
                _attempts[ip] = []

        # Clean old attempts outside window
        _attempts[ip] = [t for t in _attempts[ip] if now - t < WINDOW_SECONDS]

        if len(_attempts[ip]) >= MAX_ATTEMPTS:
            _lockouts[ip] = now + LOCKOUT_SECONDS
            _attempts[ip] = []
            return False, LOCKOUT_SECONDS

    return True, 0


def record_failed_attempt(request) -> None:
    """Call after a failed login attempt."""
    ip = _client_ip(request)
    with _lock:
        _attempts[ip].append(time.time())


def reset_attempts(request) -> None:
    """Call after a successful login."""
    ip = _client_ip(request)
    with _lock:
        _attempts.pop(ip, None)
        _lockouts.pop(ip, None)
