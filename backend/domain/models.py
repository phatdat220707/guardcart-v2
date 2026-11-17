"""Domain contracts. Valid data is not, by itself, checkout authorization."""

from datetime import datetime, timezone
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt,
    StringConstraints, computed_field, model_validator,
)


MAX_MONEY_MINOR = 1_000_000_000_000
Money = Annotated[StrictInt, Field(ge=0, le=MAX_MONEY_MINOR)]
PositiveMoney = Annotated[StrictInt, Field(gt=0, le=MAX_MONEY_MINOR)]
Count = Annotated[StrictInt, Field(ge=0, le=1_000_000_000)]
Version = Annotated[StrictInt, Field(ge=1)]
EntityId = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]
Tag = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]


def utc_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use an ISO-8601 timestamp with an explicit timezone.") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required; numeric timestamps are not accepted.")
    return value.astimezone(timezone.utc)


Timestamp = Annotated[datetime, BeforeValidator(utc_timestamp)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class Requirements(Contract):
    use_case: Tag | None = None
    surface: Tag | None = None
    size: Text | None = None


class Preference(Contract):
    attribute: Literal["brand", "cushioning", "weight_class"]
    value: Text


class MandateDraft(Contract):
    category: Literal["running_shoes"] | None = None
    max_spend_minor: PositiveMoney | None = None
    requirements: Requirements = Field(default_factory=Requirements)
    preferences: tuple[Preference, ...] = ()
    suggested_approval_threshold_minor: Money | None = None

    @computed_field
    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in ("category", "max_spend_minor") if getattr(self, name) is None)


class MandateConfirmation(Contract):
    category: Literal["running_shoes"]
    currency: Literal["HKD"] = "HKD"
    max_spend_minor: PositiveMoney
    auto_approve_enabled: StrictBool = False
    approval_threshold_minor: Money | None = None
    max_points: Count = 0
    trusted_merchants_only: StrictBool = True
    expires_at: Timestamp
    requirements: Requirements = Field(default_factory=Requirements)
    preferences: tuple[Preference, ...] = ()

    @model_validator(mode="after")
    def consistent_terms(self) -> Self:
        threshold = self.approval_threshold_minor
        if self.auto_approve_enabled:
            if threshold is None or threshold > self.max_spend_minor:
                raise ValueError("Enabled auto approval requires a threshold within the spending limit.")
        elif threshold is not None:
            raise ValueError("Disabled auto approval requires a null threshold.")
        names = [preference.attribute for preference in self.preferences]
        if len(names) != len(set(names)):
            raise ValueError("Each preference attribute can occur only once.")
        return self


class ConfirmedMandate(MandateConfirmation):
    id: EntityId
    session_id: EntityId
    version: Version = 1
    created_at: Timestamp
    status: Literal["ACTIVE", "CONSUMED", "REVOKED", "SUPERSEDED"] = "ACTIVE"

    @model_validator(mode="after")
    def expiry_after_creation(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("Mandate expiry must be after creation.")
        return self


class ProductAttributes(Contract):
    use_cases: tuple[Tag, ...] = ()
    surfaces: tuple[Tag, ...] = ()
    available_sizes: tuple[Text, ...] = ()
    brand: Text | None = None
    cushioning: Text | None = None
    weight_class: Text | None = None


class Merchant(Contract):
    id: EntityId
    version: Version = 1
    name: Text
    trusted: StrictBool


class Product(Contract):
    id: EntityId
    version: Version = 1
    name: Text
    category: Tag
    currency: Literal["HKD"] = "HKD"
    merchant_id: EntityId
    price_minor: Money
    shipping_minor: Money
    availability: StrictBool
    attributes: ProductAttributes = Field(default_factory=ProductAttributes)
    description: Annotated[str, StringConstraints(strict=True, max_length=4000)] = ""


class PaymentOption(Contract):
    id: EntityId
    version: Version = 1
    name: Text
    currency: Literal["HKD"] = "HKD"
    fee_minor: Money = 0
    reward_minor: Money = 0
    points_used: Count = 0
    enabled: StrictBool = True
    # None means unrestricted; an empty tuple means no merchant is eligible.
    eligible_merchant_ids: tuple[EntityId, ...] | None = None


class Quote(Contract):
    id: EntityId
    session_id: EntityId
    mandate_id: EntityId
    mandate_version: Version
    product_id: EntityId
    product_version: Version
    merchant_id: EntityId
    merchant_version: Version
    payment_id: EntityId
    payment_version: Version
    currency: Literal["HKD"] = "HKD"
    price_minor: Money
    shipping_minor: Money
    fee_minor: Money
    reward_minor: Money
    points_used: Count
    final_total_minor: PositiveMoney
    effective_cost_minor: Annotated[StrictInt, Field(ge=-MAX_MONEY_MINOR, le=MAX_MONEY_MINOR)]
    created_at: Timestamp
    expires_at: Timestamp
    status: Literal[
        "CREATED", "EVALUATED", "AUTO_ALLOWED", "AWAITING_APPROVAL", "APPROVED",
        "DECLINED", "BLOCKED", "COMPLETED", "STALE", "EXPIRED",
    ] = "CREATED"

    @model_validator(mode="after")
    def consistent_quote(self) -> Self:
        if self.final_total_minor != self.price_minor + self.shipping_minor + self.fee_minor:
            raise ValueError("Checkout total must equal price plus shipping plus fee.")
        if self.effective_cost_minor != self.final_total_minor - self.reward_minor:
            raise ValueError("Effective cost must equal checkout total minus reward.")
        if self.expires_at <= self.created_at:
            raise ValueError("Quote expiry must be after creation.")
        return self


class RuleCheck(Contract):
    key: Tag
    label: Text
    outcome: Literal["PASS", "FAIL", "ASK", "NOT_EVALUATED"]
    detail: Text


class PolicyDecision(Contract):
    id: EntityId
    mandate_id: EntityId
    mandate_version: Version
    quote_id: EntityId
    product_id: EntityId
    status: Literal["APPROVE", "ASK", "BLOCK"]
    currency: Literal["HKD"] = "HKD"
    final_total_minor: PositiveMoney
    effective_cost_minor: Annotated[StrictInt, Field(ge=-MAX_MONEY_MINOR, le=MAX_MONEY_MINOR)]
    reasons: tuple[Text, ...]
    checks: tuple[RuleCheck, ...]
    evaluated_at: Timestamp


class RankedPayment(Contract):
    payment_id: EntityId
    name: Text
    quote: Quote
    policy: PolicyDecision


class QuoteView(Contract):
    quote: Quote
    policy: PolicyDecision


class Approval(Contract):
    id: EntityId
    session_id: EntityId
    quote_id: EntityId
    mandate_id: EntityId
    mandate_version: Version
    decision: Literal["APPROVED", "DECLINED"]
    created_at: Timestamp


class CompletedTransaction(Contract):
    id: EntityId
    session_id: EntityId
    quote_id: EntityId
    mandate_id: EntityId
    mandate_version: Version
    product_id: EntityId
    payment_id: EntityId
    authorization_type: Literal["AUTO_POLICY", "USER_APPROVAL"]
    approval_id: EntityId | None = None
    currency: Literal["HKD"] = "HKD"
    final_total_minor: PositiveMoney
    effective_cost_minor: Annotated[StrictInt, Field(ge=-MAX_MONEY_MINOR, le=MAX_MONEY_MINOR)]
    idempotency_key: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
    status: Literal["COMPLETED"] = "COMPLETED"
    completed_at: Timestamp

    @model_validator(mode="after")
    def consistent_authorization(self) -> Self:
        if (self.authorization_type == "USER_APPROVAL") != (self.approval_id is not None):
            raise ValueError("Only user-authorized transactions must reference an approval.")
        return self


class AuditEvent(Contract):
    id: EntityId
    session_id: EntityId
    event_type: Tag
    entity_id: EntityId
    timestamp: Timestamp
    details: dict[str, str | StrictInt | StrictBool | None] = Field(default_factory=dict)


class SessionInfo(Contract):
    id: EntityId
    created_at: Timestamp
    expires_at: Timestamp


class Catalogue(Contract):
    merchants: tuple[Merchant, ...]
    products: tuple[Product, ...]
    payments: tuple[PaymentOption, ...]
