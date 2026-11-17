from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints

from backend.domain.models import Contract, EntityId, MandateDraft, Preference, QuoteView, Text, Timestamp


class ChatRequest(Contract):
    request_id: EntityId
    conversation_id: EntityId | None = None
    message: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]


class RecommendRequest(Contract):
    request_id: EntityId
    mandate_id: EntityId


class DraftSubmission(Contract):
    draft: MandateDraft
    clarification: Literal["ambiguous_budget", "conflicting_preferences", "unsupported_size", "unsupported_category"] | None = None


class SearchArguments(Contract):
    limit: Annotated[StrictInt, Field(ge=3, le=10)] = 5


class ProductArguments(Contract):
    product_id: EntityId


class FinishArguments(Contract):
    product_ids: Annotated[tuple[EntityId, ...], Field(max_length=3)]


class ToolTrace(Contract):
    name: str
    arguments: dict
    outcome: Literal["OK"] = "OK"


class RunMetadata(Contract):
    run_id: EntityId
    requested_model: str
    provider_calls: int
    elapsed_ms: int
    usage_verified: Literal[False] = False


class ChatResult(Contract):
    conversation_id: EntityId
    run: RunMetadata
    message: str
    draft: MandateDraft
    needs_clarification: bool
    requires_confirmation: Literal[True] = True


class ChatMessage(Contract):
    role: Literal["user", "assistant"]
    content: str
    created_at: Timestamp


class ConversationView(Contract):
    id: EntityId
    messages: tuple[ChatMessage, ...]
    draft: MandateDraft | None


class Recommendation(Contract):
    product_id: EntityId
    name: Text
    merchant_name: Text
    explanation: str
    matched_preferences: tuple[Preference, ...]
    unmatched_preferences: tuple[Preference, ...]
    unknown_preferences: tuple[Preference, ...]
    quote: QuoteView


class RecommendationResult(Contract):
    mandate_id: EntityId
    run: RunMetadata
    message: str
    recommendations: tuple[Recommendation, ...]
    tool_trace: tuple[ToolTrace, ...]


class RunView(Contract):
    id: EntityId
    kind: Literal["CHAT", "RECOMMEND"]
    state: Literal["RUNNING", "COMPLETED", "FAILED"]
    requested_model: str
    provider_calls: int
    error_code: str | None
    created_at: Timestamp
    usage_verified: Literal[False] = False


class UsageView(Contract):
    calls_used: int
    session_call_limit: int
    calls_remaining: int
    usage_verified: Literal[False] = False
