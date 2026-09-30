"""Slug generation for tenant names (Client, Agency, StationAccount)."""

import re


def slugify(name: str) -> str:
    """
    Turn a display name into a URL-safe slug.

        "Pick & Pay"      -> "pick-pay"
        "Oracle Sun!!"    -> "oracle-sun"
        "  5FM  Radio "   -> "5fm-radio"

    Lowercases, replaces any run of non-alphanumeric chars with a single
    hyphen, and trims leading/trailing hyphens. Returns "" for empty/garbage
    input (caller decides what to do with that).
    """
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s
