"""Public HTTP inputs have no fields for prices, trust or policy overrides."""

from typing import Annotated

from pydantic import Field, StrictBool, StrictInt

from backend.domain.models import Contract, EntityId, SessionInfo


class SessionResponse(SessionInfo):
    csrf_token: str


class QuoteRequest(Contract):
    mandate_id: EntityId
    product_id: EntityId
    payment_id: EntityId
    ttl_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 300


class ApprovalRequest(Contract):
    approve: StrictBool


class EmptyCommand(Contract):
    pass
