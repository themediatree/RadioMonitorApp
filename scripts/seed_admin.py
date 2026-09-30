"""
Seed the first internal admin user.

Usage (from D:\\RadioMonitorApp with venv active):
    python scripts/seed_admin.py

Interactive: prompts for email + password (hidden) + full name.
Idempotent: if the email already exists, prints a notice and exits.

You only need to run this ONCE. After that, log in as this user and create
other users through the (eventual) admin UI.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

# Make 'app' importable when running as `python scripts/seed_admin.py`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.password import (  # noqa: E402
    PasswordPolicyError,
    hash_password,
    validate_password,
)
from app.database import session_scope, check_connection  # noqa: E402
from app.models.user import User, UserType  # noqa: E402


def prompt_email() -> str:
    while True:
        email = input("Email: ").strip().lower()
        if "@" in email and "." in email.split("@", 1)[1]:
            return email
        print("  → That doesn't look like an email. Try again.")


def prompt_password() -> str:
    while True:
        pw = getpass.getpass("Password: ")
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            print("  → Passwords don't match. Try again.")
            continue
        try:
            validate_password(pw)
        except PasswordPolicyError as e:
            print(f"  → {e}")
            continue
        return pw


def main() -> int:
    print("=" * 60)
    print("RadioMonitor -- seed internal admin user")
    print("=" * 60)

    if not check_connection():
        print(
            "ERROR: Cannot connect to the database. Check your .env file "
            "(DB_SERVER, DB_NAME, DB_TRUSTED_CONNECTION, etc.) and that "
            "the SQL Server instance is reachable.",
            file=sys.stderr,
        )
        return 1

    email = prompt_email()

    with session_scope() as db:
        existing = db.query(User).filter(User.Email == email).one_or_none()
        if existing:
            print(
                f"\nA user with email {email!r} already exists "
                f"(UserID={existing.UserID}, type={existing.UserType}). "
                f"Nothing to do."
            )
            return 0

        full_name = input("Full name (optional): ").strip() or None
        password = prompt_password()

        user = User(
            Email=email,
            PasswordHash=hash_password(password),
            FullName=full_name,
            UserType=UserType.INTERNAL.value,
            ClientID=None,
            AgencyID=None,
            IsActive=True,
            EmailVerified=True,    # we trust the operator; they just ran a script
        )
        db.add(user)
        db.flush()
        print(f"\n✓ Created internal user UserID={user.UserID} ({user.Email}).")
        print("  You can now `python main.py` and log in at /login.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
