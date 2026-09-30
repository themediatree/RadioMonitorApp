"""
Password hashing and verification.

Uses the `bcrypt` library directly. We dropped passlib: it is unmaintained
(last release 2020) and breaks against bcrypt >= 4.1 / 5.x, which is what
ships wheels for current Python versions (3.13/3.14).

bcrypt only considers the first 72 BYTES of input. To avoid silent
truncation (and the hard error newer bcrypt raises on >72 bytes), we
pre-hash the password with SHA-256 and base64-encode the digest before
handing it to bcrypt. The digest is a fixed 44 bytes, always under the
limit, and collapses arbitrarily long passwords safely.

    stored = bcrypt( base64( sha256(password) ), salt, cost=12 )

This is a deliberate, stable scheme. Every hash produced by hash_password()
is verifiable by verify_password() and vice versa.

Cost factor 12 is ~200-300ms on a modern server -- appropriate for an
interactive login, painful for brute force.
"""

import base64
import hashlib

import bcrypt


# bcrypt cost (work factor). 12 is a sensible default for web logins.
_BCRYPT_ROUNDS = 12


def _prepare(plain: str) -> bytes:
    """
    Collapse a password of any length to a fixed 44-byte token that bcrypt
    can hash without hitting its 72-byte limit.

    SHA-256 -> 32 raw bytes -> base64 -> 44 bytes. We base64 (rather than use
    raw digest bytes) so there are no NUL bytes, which bcrypt treats as a
    string terminator.
    """
    digest = hashlib.sha256(plain.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(plain: str) -> str:
    """
    Hash a plaintext password. Use only at user creation / password change.
    Returns a str (utf-8) suitable for storing in User.PasswordHash.
    """
    prepared = _prepare(plain)
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(prepared, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verify a plaintext password against a stored hash.
    Returns False on any error (malformed hash, wrong algorithm, etc.)
    rather than raising, so callers don't have to wrap this in try/except.
    """
    try:
        prepared = _prepare(plain)
        return bcrypt.checkpw(prepared, hashed.encode("utf-8"))
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    """
    True if the stored hash uses a lower cost factor than our current target
    and should be rehashed on next successful login.

    bcrypt hashes look like: $2b$<cost>$<22-char-salt><31-char-digest>
    We parse out <cost> and compare to _BCRYPT_ROUNDS.
    """
    try:
        parts = hashed.split("$")
        # parts = ['', '2b', '12', '<salt+digest>']
        cost = int(parts[2])
        return cost < _BCRYPT_ROUNDS
    except Exception:
        # If we can't parse it, treat it as needing a rehash.
        return True


class PasswordPolicyError(ValueError):
    """Raised when a proposed password doesn't meet policy."""


def validate_password(password: str) -> None:
    """
    Enforce minimum password policy. Called at user creation and password
    change. Raises PasswordPolicyError with a user-friendly message on failure.

    Current rules (intentionally simple, expand later):
        - At least settings.password_min_length characters
        - Must contain at least one letter and one digit
        - Must not start or end with whitespace
    """
    from app.config import settings

    if not password or password.strip() != password:
        raise PasswordPolicyError("Password must not start or end with whitespace.")
    if len(password) < settings.password_min_length:
        raise PasswordPolicyError(
            f"Password must be at least {settings.password_min_length} characters."
        )
    if not any(c.isalpha() for c in password):
        raise PasswordPolicyError("Password must contain at least one letter.")
    if not any(c.isdigit() for c in password):
        raise PasswordPolicyError("Password must contain at least one number.")
