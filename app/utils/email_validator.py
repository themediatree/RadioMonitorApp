"""
app/utils/email_validator.py

Lightweight email validation — no external library needed.
Checks format and known-bad patterns without DNS lookups.
"""

import re

# RFC 5322 simplified: local@domain.tld
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

_BLOCKED_DOMAINS = {
    "example.com", "example.org", "example.net",
    "invalid.com",
}

# SA mobile: 10 digits starting 0, or international +27 followed by 9 digits
# Accepts spaces/hyphens as separators
_PHONE_RE = re.compile(
    r"^(?:(\+27|0027)\s?|0)"   # international +27/0027 or local 0
    r"([6-8][0-9])"             # SA mobile prefix (60-89)
    r"[\s\-]?"
    r"([0-9]{3})"
    r"[\s\-]?"
    r"([0-9]{4})$"
)


class EmailValidationError(ValueError):
    pass


def validate_email(email: str) -> str:
    """
    Validate and normalise an email address.
    Returns the lowercased, stripped email.
    Raises EmailValidationError with a user-friendly message on failure.
    """
    if not email or not isinstance(email, str):
        raise EmailValidationError("An email address is required.")

    email = email.strip().lower()

    if not _EMAIL_RE.match(email):
        raise EmailValidationError(
            f"{email!r} is not a valid email address. "
            "Please check the format (e.g. name@company.co.za)."
        )

    domain = email.split("@", 1)[1]
    if domain in _BLOCKED_DOMAINS:
        raise EmailValidationError(
            f"Please use a real email address, not {domain!r}."
        )

    if len(email) > 254:
        raise EmailValidationError("Email address is too long.")

    return email


class PhoneValidationError(ValueError):
    pass


def validate_phone(phone: str) -> str:
    """
    Validate and normalise a South African mobile number.
    Accepts: 0821234567, +27821234567, 0027821234567, spaces/hyphens as separators.
    Returns normalised form: 0821234567 (10-digit local format).
    Raises PhoneValidationError on failure.
    Phone is optional in the system — only validate if non-empty.
    """
    if not phone or not isinstance(phone, str):
        return ""

    phone = phone.strip()
    if not phone:
        return ""

    # Strip separators for matching
    clean = phone.replace(" ", "").replace("-", "")
    m = _PHONE_RE.match(clean)
    if not m:
        raise PhoneValidationError(
            f"{phone!r} is not a valid South African mobile number. "
            "Expected format: 082 123 4567 or +27 82 123 4567."
        )

    # Normalise to 10-digit local format
    prefix, mid, last = m.group(2), m.group(3), m.group(4)
    return f"0{prefix}{mid}{last}"
