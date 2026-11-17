// Only opaque request identifiers are persisted; prompts and credentials stay out.
const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

export function pendingRequest(value) {
  return value && ID.test(value.requestId || "") && ["CHAT", "RECOMMEND"].includes(value.kind)
    ? { requestId: value.requestId, kind: value.kind } : null;
}

export function resultMatches(kind, result) {
  if (!result || !ID.test(result.run?.run_id || "")) return false;
  if (kind === "CHAT") return ID.test(result.conversation_id || "") &&
    typeof result.message === "string" && result.requires_confirmation === true &&
    result.draft !== null && typeof result.draft === "object";
  return kind === "RECOMMEND" && ID.test(result.mandate_id || "") &&
    Array.isArray(result.recommendations) && Array.isArray(result.tool_trace) &&
    result.recommendations.every((item) => ID.test(item?.quote?.quote?.id || ""));
}
