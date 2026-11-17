"""Offline policy demonstration. No AI, storage, checkout or payment calls."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.domain.models import ConfirmedMandate, Merchant, PaymentOption, Product
from backend.domain.policy import create_quote, evaluate_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto-approve", action="store_true", help="Explicitly enable the demo's HK$750 auto-approval threshold.")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    mandate = ConfirmedMandate(
        id="demo-mandate", session_id="demo-session", category="running_shoes",
        max_spend_minor=80000, auto_approve_enabled=args.auto_approve,
        approval_threshold_minor=75000 if args.auto_approve else None,
        created_at=now, expires_at=now + timedelta(hours=1),
    )
    merchant = Merchant(id="demo-merchant", name="Demo Merchant", trusted=True)
    payment = PaymentOption(id="demo-card", name="Demo Card", reward_minor=2500)
    mode = "ENABLED up to HK$750" if args.auto_approve else "DISABLED: human approval required"
    print("Auto approval: " + mode)
    print("Maximum spend: HK$800 | All data simulated | No purchase is executed")
    for product_id, price, shipping in (("P01", 69900, 3000), ("P02", 76000, 1000), ("P04_DEMO", 80000, 2000)):
        product = Product(id=product_id, name="Demo Running Shoe", category="running_shoes",
                          merchant_id=merchant.id, price_minor=price, shipping_minor=shipping, availability=True)
        quote = create_quote(mandate, product, merchant, payment, now=now)
        decision = evaluate_policy(mandate, quote, product, merchant, payment, now=now)
        # Formatting only; all calculations above use integer minor units.
        total = f"{quote.final_total_minor // 100}.{quote.final_total_minor % 100:02d}"
        print(f"HK${total} -> {decision.status}: {decision.reasons[0]}")
    print("Policy evaluation only: the demo does not complete or consume a mandate.")


if __name__ == "__main__":
    main()
