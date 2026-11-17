from datetime import datetime, timedelta, timezone

from backend.domain.models import ConfirmedMandate, Merchant, PaymentOption, Product


NOW = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


def mandate(**updates):
    values = dict(
        id="mandate-1", session_id="session-1", category="running_shoes",
        max_spend_minor=80000, created_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(days=1),
    )
    values.update(updates)
    return ConfirmedMandate.model_validate(values)


def product(**updates):
    values = dict(
        id="P01", name="Demo Running Shoe", merchant_id="merchant-1",
        category="running_shoes", price_minor=69900, shipping_minor=3000,
        availability=True,
    )
    values.update(updates)
    return Product.model_validate(values)


def merchant(**updates):
    return Merchant.model_validate({"id": "merchant-1", "name": "Demo Merchant", "trusted": True, **updates})


def payment(**updates):
    return PaymentOption.model_validate({"id": "card-1", "name": "Demo Card", "reward_minor": 2500, **updates})


def updated(model, **changes):
    return type(model).model_validate({**model.model_dump(), **changes})
