#!/usr/bin/env python
"""
scripts/credit_monthly_tokens.py

Credits monthly token allocations for Enterprise and Premium subscribers.
Run on the 1st of each month via Windows Task Scheduler.

Usage:
    python scripts/credit_monthly_tokens.py [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("radiomonitor.monthly_credit")


def main(dry_run: bool = False) -> None:
    from app.database import SessionLocal
    from app.services.token_service import credit_monthly_allocations

    logger.info("Monthly token credit job starting (dry_run=%s)", dry_run)

    with SessionLocal() as db:
        if dry_run:
            # Show what would be credited without committing.
            from app.models.subscriber import Subscriber
            from app.models.subscription_plan import SubscriptionPlanConfig
            subs = (
                db.query(Subscriber)
                .join(SubscriptionPlanConfig,
                      SubscriptionPlanConfig.PlanCode == Subscriber.SubscriptionPlan)
                .filter(
                    Subscriber.SubscriberStatus.in_(["active", "expired_grace"]),
                    SubscriptionPlanConfig.TokensPerMonth.isnot(None),
                    SubscriptionPlanConfig.ContractMonths > 0,
                )
                .all()
            )
            logger.info("[DRY RUN] Would credit %d subscribers:", len(subs))
            for sub in subs:
                plan = db.get(SubscriptionPlanConfig, sub.SubscriptionPlan)
                logger.info(
                    "  %s (ID=%d) — %.2f tokens (%s)",
                    sub.Name, sub.SubscriberID,
                    float(plan.TokensPerMonth), plan.DisplayName,
                )
        else:
            credited = credit_monthly_allocations(db)
            db.commit()
            logger.info("Credited %d subscriber(s).", len(credited))
            for sub_id in credited:
                logger.info("  SubscriberID=%d", sub_id)

    logger.info("Monthly token credit job complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be credited without committing")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
