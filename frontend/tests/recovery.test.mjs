import test from "node:test";
import assert from "node:assert/strict";
import { pendingRequest, resultMatches } from "../dist/recovery.mjs";

test("only a valid request identifier and kind can be restored", () => {
  assert.equal(pendingRequest({ requestId: "../secret", kind: "CHAT" }), null);
  assert.equal(pendingRequest({ requestId: "ok", kind: "CHECKOUT" }), null);
  assert.deepEqual(pendingRequest({ requestId: "req-1", kind: "CHAT", message: "private", key: "secret" }),
    { requestId: "req-1", kind: "CHAT" });
});

test("chat result cannot be restored as recommendations", () => {
  const chat = { run: { run_id: "run-1" }, conversation_id: "conv-1", message: "Review draft",
    requires_confirmation: true, draft: { max_spend_minor: 80000 } };
  assert.equal(resultMatches("CHAT", chat), true);
  assert.equal(resultMatches("RECOMMEND", chat), false);
  assert.equal(resultMatches("CHAT", { ...chat, requires_confirmation: false }), false);
});

test("empty recommendations and valid quote identifiers can be recovered", () => {
  const result = { run: { run_id: "run-2" }, mandate_id: "mandate-1", recommendations: [], tool_trace: [] };
  assert.equal(resultMatches("RECOMMEND", result), true);
  assert.equal(resultMatches("RECOMMEND", { ...result, recommendations: [{}] }), false);
  assert.equal(resultMatches("RECOMMEND", { ...result, recommendations: [{ quote: { quote: { id: "quote-1" } } }] }), true);
});
