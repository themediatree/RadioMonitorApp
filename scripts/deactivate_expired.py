"""
Deactivate expired campaigns and commercials: flip Campaign.IsActive -> False
and Commercial.Status -> 'completed' once their EndDate has passed.

Usage (from D:\\RadioMonitorApp with venv active):
    python scripts/deactivate_expired.py            # do it
    python scripts/deactivate_expired.py --dry-run  # report only, no change

Schedule: run nightly AFTER midnight (00:05 AM recommended) so that campaigns
ending today are deactivated before the pipeline's next library rebuild.
The pipeline's library_manager excludes Status != 'active' commercials.

Exit code 0 on success (including "nothing to do"), 1 on error.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import session_scope, check_connection  # noqa: E402
from app.models.campaign import Campaign, CampaignCommercial, Commercial  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    today = datetime.now().date()

    if not check_connection():
        print("ERROR: cannot connect to the database. Check .env / SQL Server.")
        return 1

    try:
        with session_scope() as db:

            # ── 1. Campaigns whose EndDate has passed ──────────────────────
            expired_campaigns = (
                db.query(Campaign)
                .filter(
                    Campaign.IsActive == True,
                    Campaign.EndDate != None,
                    Campaign.EndDate < today,
                )
                .all()
            )

            # ── 2. Commercials whose ALL campaigns have ended ──────────────
            # A commercial is expired if every campaign it belongs to is
            # either already inactive OR ending today/earlier.
            all_active_commercials = (
                db.query(Commercial)
                .filter(Commercial.IsActive == True)
                .all()
            )

            expired_commercials = []
            for c in all_active_commercials:
                # Find all campaigns this commercial belongs to
                links = (
                    db.query(CampaignCommercial)
                    .filter(CampaignCommercial.CommercialID == c.CommercialID)
                    .all()
                )
                if not links:
                    continue  # not linked to any campaign — leave alone

                campaign_ids = [lk.CampaignID for lk in links]
                campaigns = db.query(Campaign).filter(
                    Campaign.CampaignID.in_(campaign_ids)
                ).all()

                # Commercial expires only if ALL its campaigns have ended
                all_ended = all(
                    (not camp.IsActive or
                     (camp.EndDate is not None and camp.EndDate < today))
                    for camp in campaigns
                )
                if all_ended:
                    expired_commercials.append(c)

            if dry_run:
                if not expired_campaigns and not expired_commercials:
                    print("[dry-run] Nothing to deactivate today.")
                else:
                    if expired_campaigns:
                        print(f"[dry-run] Would deactivate {len(expired_campaigns)} campaign(s):")
                        for camp in expired_campaigns:
                            print(f"    CampaignID={camp.CampaignID}  {camp.Name}  EndDate={camp.EndDate}")
                    if expired_commercials:
                        print(f"[dry-run] Would expire {len(expired_commercials)} commercial(s):")
                        for c in expired_commercials:
                            print(f"    CommercialID={c.CommercialID}  {c.CommercialName}")
                db.rollback()
                return 0

            # ── Apply changes ──────────────────────────────────────────────
            for camp in expired_campaigns:
                camp.IsActive = False
                print(f"Campaign deactivated: [{camp.CampaignID}] {camp.Name}")

            for c in expired_commercials:
                c.IsActive = False
                c.Status = "completed"
                print(f"Commercial expired: [{c.CommercialID}] {c.CommercialName}")

            if not expired_campaigns and not expired_commercials:
                print("Nothing to deactivate today.")
            else:
                print(
                    f"Done — {len(expired_campaigns)} campaign(s) deactivated, "
                    f"{len(expired_commercials)} commercial(s) expired."
                )

            # session_scope commits on clean exit
        return 0

    except Exception as e:  # noqa: BLE001
        print(f"ERROR during deactivation: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
