"""Stored simulated checkout demo. No AI or external payment requests."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.domain.models import MandateConfirmation, Merchant, PaymentOption, Product
from backend.services.checkout import CheckoutService
from backend.services.errors import Conflict
from backend.storage import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "var" / "checkout-demo.sqlite3")
    parser.add_argument("--auto-approve", action="store_true", help="Enable the demo mandate's HK$750 auto threshold.")
    parser.add_argument("--approve", action="store_true", help="Simulate explicit user approval for ASK quotes.")
    args = parser.parse_args()
    service = CheckoutService(Database(args.database))
    products = tuple(Product(
        id=product_id, name="Demo Running Shoe", category="running_shoes", merchant_id="demo-merchant",
        price_minor=price, shipping_minor=shipping, availability=True,
    ) for product_id, price, shipping in (("P01", 69900, 3000), ("P02", 76000, 1000), ("P04_DEMO", 80000, 2000)))
    service.install_catalogue(
        merchants=(Merchant(id="demo-merchant", name="Demo Merchant", trusted=True),), products=products,
        payments=(PaymentOption(id="demo-card", name="Demo Card", reward_minor=2500),),
    )
    token = service.create_session()  # Never print or put this bearer token in logs.
    print("Simulation only: no external purchase or payment is made.")
    print("SQLite: " + str(args.database.resolve()))
    print("A separate one-purchase mandate is created for each scenario.")
    print("Auto approval: " + ("enabled to HK$750" if args.auto_approve else "disabled"))
    for product in products:
        mandate = service.confirm_mandate(token, MandateConfirmation(
            category="running_shoes", max_spend_minor=80000,
            auto_approve_enabled=args.auto_approve,
            approval_threshold_minor=75000 if args.auto_approve else None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        view = service.propose_quote(token, mandate.id, product.id, "demo-card")
        total = view.quote.final_total_minor
        print(f"\nHK${total // 100}.{total % 100:02d} -> {view.policy.status}")
        if view.policy.status == "BLOCK":
            print("  Blocked; no approval can override the spending cap.")
            continue
        if view.policy.status == "ASK":
            if not args.approve:
                print("  Waiting for user approval; no transaction created.")
                continue
            service.decide_quote(token, view.quote.id, approve=True)
            print("  Explicit user approval simulated by the --approve flag.")
        key = "demo-" + product.id
        transaction = service.checkout(token, view.quote.id, idempotency_key=key)
        replay = service.checkout(token, view.quote.id, idempotency_key=key)
        assert transaction == replay
        assert service.get_mandate(token, mandate.id).status == "CONSUMED"
        try:
            service.checkout(token, view.quote.id, idempotency_key=key + "-again")
        except Conflict:
            print("  Completed once; same-key retry returns the original; a new purchase is rejected.")
        else:
            raise AssertionError("A consumed mandate was used twice.")
        print("  Authorization: " + transaction.authorization_type)
    print(f"\nCompleted simulated transactions in this session: {len(service.list_transactions(token))}")
    print(f"Stored audit events in this session: {len(service.list_audit(token))}")


if __name__ == "__main__":
    main()
