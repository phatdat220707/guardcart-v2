"""Pure policy evaluation; persistence, approval and checkout live in services.

Callers must load authoritative catalogue records and the confirmed mandate
from trusted backend storage. Accepting these objects from a browser or model
does not establish ownership or authorization.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .models import (
    ConfirmedMandate, Merchant, PaymentOption, PolicyDecision, Product,
    MAX_MONEY_MINOR, Quote, RankedPayment, RuleCheck, utc_timestamp,
)


ACTIVE_QUOTE_STATUSES = {"CREATED", "EVALUATED", "AUTO_ALLOWED", "AWAITING_APPROVAL", "APPROVED"}


def _clock(now: datetime | None) -> datetime:
    return utc_timestamp(now if now is not None else datetime.now(timezone.utc))


def create_quote(
    mandate: ConfirmedMandate, product: Product, merchant: Merchant, payment: PaymentOption,
    *, now: datetime | None = None, ttl_seconds: int = 300,
) -> Quote:
    timestamp = _clock(now)
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
        raise ValueError("Quote lifetime must be an integer between 1 and 3600 seconds.")
    if timestamp < mandate.created_at or timestamp >= mandate.expires_at:
        raise ValueError("Cannot create a quote outside the mandate validity period.")
    if product.merchant_id != merchant.id:
        raise ValueError("Product and merchant do not match.")
    total = product.price_minor + product.shipping_minor + payment.fee_minor
    return Quote(
        id=str(uuid4()), session_id=mandate.session_id, mandate_id=mandate.id,
        mandate_version=mandate.version, product_id=product.id, product_version=product.version,
        merchant_id=merchant.id, merchant_version=merchant.version,
        payment_id=payment.id, payment_version=payment.version,
        price_minor=product.price_minor, shipping_minor=product.shipping_minor,
        fee_minor=payment.fee_minor, reward_minor=payment.reward_minor, points_used=payment.points_used,
        final_total_minor=total, effective_cost_minor=total - payment.reward_minor,
        created_at=timestamp, expires_at=min(timestamp + timedelta(seconds=ttl_seconds), mandate.expires_at),
    )


def evaluate_policy(
    mandate: ConfirmedMandate, quote: Quote, product: Product, merchant: Merchant,
    payment: PaymentOption, *, now: datetime | None = None,
) -> PolicyDecision:
    timestamp = _clock(now)
    checks: list[RuleCheck] = []
    failures: list[str] = []

    def check(key: str, label: str, passed: bool, success: str, failure: str) -> None:
        checks.append(RuleCheck(key=key, label=label, outcome="PASS" if passed else "FAIL",
                                detail=success if passed else failure))
        if not passed:
            failures.append(failure)

    check("mandate_active", "Mandate is active", mandate.status == "ACTIVE",
          "Mandate is active.", "Mandate is consumed, revoked or superseded.")
    check("mandate_time", "Mandate validity period", mandate.created_at <= timestamp < mandate.expires_at,
          "Mandate is currently valid.", "Mandate is expired or not yet valid.")
    check("quote_binding", "Quote belongs to this mandate and session",
          quote.session_id == mandate.session_id and quote.mandate_id == mandate.id
          and quote.mandate_version == mandate.version,
          "Quote matches the mandate and session.", "Quote does not match the current mandate, version or session.")
    check("quote_state", "Quote is open for evaluation", quote.status in ACTIVE_QUOTE_STATUSES,
          "Quote is open.", "Quote is closed, stale or already completed.")
    check("quote_time", "Quote validity period",
          mandate.created_at <= quote.created_at <= timestamp < quote.expires_at <= mandate.expires_at,
          "Quote is currently valid.", "Quote is expired, not yet valid or outside the mandate validity period.")
    snapshot_matches = (
        quote.product_id == product.id and quote.product_version == product.version
        and product.merchant_id == merchant.id == quote.merchant_id
        and quote.merchant_version == merchant.version
        and quote.payment_id == payment.id and quote.payment_version == payment.version
        and quote.price_minor == product.price_minor and quote.shipping_minor == product.shipping_minor
        and quote.fee_minor == payment.fee_minor and quote.reward_minor == payment.reward_minor
        and quote.points_used == payment.points_used
    )
    check("catalogue_snapshot", "Quote matches current catalogue data", snapshot_matches,
          "Quote matches current product, merchant and payment records.",
          "Catalogue data changed or does not match; request a new quote.")
    check("availability", "Product is available", product.availability,
          "Product is in stock.", "Product is unavailable.")
    check("category", "Product category is authorized", product.category == mandate.category,
          "Product category matches the mandate.", "Product category is outside the mandate.")
    check("merchant_trust", "Merchant trust requirement",
          not mandate.trusted_merchants_only or merchant.trusted,
          "Merchant satisfies the trust requirement.", "Merchant is not trusted under this mandate.")
    payment_allowed = payment.enabled and (
        payment.eligible_merchant_ids is None or merchant.id in payment.eligible_merchant_ids
    )
    check("payment_eligibility", "Payment method is eligible", payment_allowed,
          "Payment method is enabled and eligible.", "Payment method is disabled or not eligible for this merchant.")
    check("maximum_spend", "Checkout total is within budget", quote.final_total_minor <= mandate.max_spend_minor,
          "Checkout total is within the maximum spend.", "Checkout total exceeds the maximum spend.")
    check("points_limit", "Points usage is authorized", quote.points_used <= mandate.max_points,
          "Points usage is within the allowance.", "Points usage exceeds the allowance.")
    required = mandate.requirements
    for key, expected, actual, label in (
        ("required_use_case", required.use_case, product.attributes.use_cases, "Required use case"),
        ("required_surface", required.surface, product.attributes.surfaces, "Required surface"),
        ("required_size", required.size, product.attributes.available_sizes, "Required size"),
    ):
        check(key, label, expected is None or expected in actual,
              "Catalogue evidence satisfies the requirement.", label + " is not supported by catalogue evidence.")

    if failures:
        status = "BLOCK"
        reasons = tuple(failures)
        checks.append(RuleCheck(key="approval", label="Human approval requirement", outcome="NOT_EVALUATED",
                                detail="A mandatory rule failed; approval cannot override it."))
    elif not mandate.auto_approve_enabled:
        status = "ASK"
        reasons = ("Automatic approval is disabled; explicit user approval is required.",)
        checks.append(RuleCheck(key="approval", label="Human approval requirement", outcome="ASK", detail=reasons[0]))
    elif quote.final_total_minor > mandate.approval_threshold_minor:
        status = "ASK"
        reasons = ("Checkout total exceeds the automatic approval threshold; user approval is required.",)
        checks.append(RuleCheck(key="approval", label="Human approval requirement", outcome="ASK", detail=reasons[0]))
    else:
        status = "APPROVE"
        reasons = ("All mandatory checks pass and checkout total is within the user-enabled automatic approval threshold.",)
        checks.append(RuleCheck(key="approval", label="Human approval requirement", outcome="PASS", detail=reasons[0]))

    return PolicyDecision(
        id=str(uuid4()), mandate_id=mandate.id, mandate_version=mandate.version,
        quote_id=quote.id, product_id=product.id, status=status,
        final_total_minor=quote.final_total_minor, effective_cost_minor=quote.effective_cost_minor,
        reasons=reasons, checks=tuple(checks), evaluated_at=timestamp,
    )


def rank_payment_options(
    mandate: ConfirmedMandate, product: Product, merchant: Merchant,
    payments: tuple[PaymentOption, ...], *, now: datetime | None = None,
) -> tuple[RankedPayment, ...]:
    """Rank eligible options only. ASK is eligible but still requires approval."""
    timestamp = _clock(now)
    ids = [payment.id for payment in payments]
    if len(ids) != len(set(ids)):
        raise ValueError("Payment option IDs must be unique.")
    ranked = []
    for payment in payments:
        if not 0 < product.price_minor + product.shipping_minor + payment.fee_minor <= MAX_MONEY_MINOR:
            continue
        quote = create_quote(mandate, product, merchant, payment, now=timestamp)
        decision = evaluate_policy(mandate, quote, product, merchant, payment, now=timestamp)
        if decision.status != "BLOCK":
            ranked.append(RankedPayment(payment_id=payment.id, name=payment.name, quote=quote, policy=decision))
    ranked.sort(key=lambda item: (item.quote.effective_cost_minor, item.quote.final_total_minor, item.payment_id))
    return tuple(ranked)
