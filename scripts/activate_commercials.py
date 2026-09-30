"""
Activate due commercials: flip 'pending' -> 'active' once their campaign's
StartDate has arrived.

Usage (from D:\\RadioMonitorApp with venv active):
    python scripts/activate_commercials.py            # do it
    python scripts/activate_commercials.py --dry-run  # report only, no change

MUST be scheduled to run nightly BEFORE the pipeline's library rebuild, so that
commercials whose campaign starts today are 'active' in time to be fingerprinted
that same night. (Pipeline's library_manager includes only Status='active'.)

Exit code 0 on success (including "nothing to do"), 1 on error.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import session_scope, check_connection  # noqa: E402
from app.services import registration_service  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not check_connection():
        print("ERROR: cannot connect to the database. Check .env / SQL Server.")
        return 1

    try:
        with session_scope() as db:
            if dry_run:
                # Compute what WOULD activate without committing.
                from datetime import datetime
                from app.models.campaign import Campaign, CampaignCommercial, Commercial
                today = datetime.now().date()
                due = (
                    db.query(Commercial)
                    .join(CampaignCommercial,
                          CampaignCommercial.CommercialID == Commercial.CommercialID)
                    .join(Campaign,
                          Campaign.CampaignID == CampaignCommercial.CampaignID)
                    .filter(Commercial.Status == "pending")
                    .filter(Campaign.StartDate <= today)
                    .distinct()
                    .all()
                )
                if not due:
                    print("[dry-run] Nothing to activate today.")
                else:
                    print(f"[dry-run] Would activate {len(due)} commercial(s):")
                    for c in due:
                        print(f"    {c.CommercialID}  {c.CommercialName}  ({c.CommercialType})")
                db.rollback()
                return 0

            activated = registration_service.activate_due_commercials(db)
            # session_scope commits on clean exit.
            if not activated:
                print("Nothing to activate today.")
            else:
                print(f"Activated {len(activated)} commercial(s): {activated}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"ERROR during activation: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
