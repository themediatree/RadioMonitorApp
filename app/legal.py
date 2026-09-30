"""
app/legal.py

Versioned Terms & Conditions text for commercial registration.

Bump TERMS_VERSION whenever the wording changes. The version and a SHA256 of
the text are recorded in AuditLog at registration time so any dispute can
recover exactly which terms the registrant agreed to.
"""

import hashlib

TERMS_VERSION = "1.0"

TERMS_TEXT = (
    "I confirm that I am the lawful owner of this commercial, or have explicit "
    "authority from the lawful owner to upload it for detection and proof-of-broadcast "
    "purposes. I grant RadioMonitor authority to track, detect, record, and extract "
    "clips of this commercial when it airs on radio stations, and to deliver proof-of-"
    "broadcast reports to me as a subscribing entity."
)

TERMS_TEXT_HASH = hashlib.sha256(TERMS_TEXT.encode()).hexdigest()
