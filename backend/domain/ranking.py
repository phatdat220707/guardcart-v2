"""Catalogue-evidence ranking: mandatory fit, preferences, then effective cost."""

from datetime import datetime

from backend.domain.models import Catalogue, ConfirmedMandate, Contract, Merchant, PaymentOption, Preference, Product, RankedPayment
from backend.domain.policy import rank_payment_options


class Candidate(Contract):
    product: Product
    merchant: Merchant
    payment: PaymentOption
    ranked_payment: RankedPayment
    matched_preferences: tuple[Preference, ...]
    unmatched_preferences: tuple[Preference, ...]
    unknown_preferences: tuple[Preference, ...]


def rank_catalogue(mandate: ConfirmedMandate, catalogue: Catalogue, *, now: datetime, limit: int = 3) -> tuple[Candidate, ...]:
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Recommendation limit must be between 1 and 20.")
    merchants = {merchant.id: merchant for merchant in catalogue.merchants}
    payments = {payment.id: payment for payment in catalogue.payments}
    if (len(merchants) != len(catalogue.merchants) or len(payments) != len(catalogue.payments)
            or len({product.id for product in catalogue.products}) != len(catalogue.products)):
        raise ValueError("Catalogue IDs must be unique.")
    candidates = []
    for product in catalogue.products:
        merchant = merchants.get(product.merchant_id)
        if merchant is None:
            raise ValueError("Product refers to an unknown merchant.")
        ranked = rank_payment_options(mandate, product, merchant, catalogue.payments, now=now)
        if not ranked:
            continue
        matched, unmatched, unknown = [], [], []
        for preference in mandate.preferences:
            value = getattr(product.attributes, preference.attribute)
            target = unknown if value is None else matched if value.casefold() == preference.value.casefold() else unmatched
            target.append(preference)
        candidates.append(Candidate(product=product, merchant=merchant, payment=payments[ranked[0].payment_id],
                                    ranked_payment=ranked[0], matched_preferences=tuple(matched),
                                    unmatched_preferences=tuple(unmatched), unknown_preferences=tuple(unknown)))
    candidates.sort(key=lambda item: (-len(item.matched_preferences), item.ranked_payment.quote.effective_cost_minor,
                                      item.ranked_payment.quote.final_total_minor, item.product.id))
    return tuple(candidates[:limit])


def money_text(amount):
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    return f"{sign}HK${amount // 100}.{amount % 100:02d}"


def explain(candidate: Candidate) -> str:
    quote = candidate.ranked_payment.quote
    matches = ", ".join(f"{item.attribute}={getattr(candidate.product.attributes, item.attribute)}" for item in candidate.matched_preferences)
    return ("Meets the confirmed mandatory requirements. "
            + (f"Matches preferences: {matches}. " if matches else "No additional preferences were matched. ")
            + f"Checkout {money_text(quote.final_total_minor)}; demo reward {money_text(quote.reward_minor)}; "
            + f"effective cost {money_text(quote.effective_cost_minor)}. "
            + ("Human approval is required." if candidate.ranked_payment.policy.status == "ASK" else "Within the enabled automatic approval threshold."))
