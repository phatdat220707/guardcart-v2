import test from "node:test";
import assert from "node:assert/strict";
import { quoteDisplayStatus, matchesCatalogue } from "../dist/view-state.mjs";

const NOW = Date.parse("2026-11-17T00:00:00Z");
const view = (status = "AWAITING_APPROVAL", expiry = NOW + 1) => ({ quote: {
  id: "quote-1", status, expires_at: new Date(expiry).toISOString(),
}, policy: { status: "ASK" } });

test("quote expiry changes the display at the exact boundary", () => {
  assert.equal(quoteDisplayStatus(view(), null, NOW), "ASK");
  assert.equal(quoteDisplayStatus(view("AWAITING_APPROVAL", NOW), null, NOW), "EXPIRED");
  assert.equal(quoteDisplayStatus(view("EXPIRED"), null, NOW), "EXPIRED");
  assert.equal(quoteDisplayStatus({ ...view(), quote: { ...view().quote, expires_at: "invalid" } }, null, NOW), "EXPIRED");
});

test("completed purchases stay completed after quote expiry and without a fetched receipt", () => {
  assert.equal(quoteDisplayStatus(view("COMPLETED", NOW - 1), null, NOW), "COMPLETED");
  assert.equal(quoteDisplayStatus(view(), { quote_id: "quote-1" }, NOW), "COMPLETED");
  assert.equal(quoteDisplayStatus(view(), { quote_id: "another-quote" }, NOW), "ASK");
});

test("declined and stale quotes do not display their old ASK policy as actionable", () => {
  assert.equal(quoteDisplayStatus(view("DECLINED", NOW - 1), null, NOW), "DECLINED");
  assert.equal(quoteDisplayStatus(view("STALE"), null, NOW), "STALE");
  assert.equal(quoteDisplayStatus(null), null);
});

test("active quotes retain the backend policy result", () => {
  for (const status of ["APPROVE", "ASK", "BLOCK"]) {
    assert.equal(quoteDisplayStatus({ ...view(), policy: { status } }, null, NOW), status);
  }
});

test("catalogue search accepts the visible product ID, name, purpose and brand", () => {
  const product = { id: "P01", name: "RoadLab Daily", attributes: { brand: "RoadLab", use_cases: ["long_distance"] } };
  for (const query of ["p01", " P01 ", "daily", "roadlab", "long_distance", ""]) assert.equal(matchesCatalogue(product, query), true);
  assert.equal(matchesCatalogue(product, "P04"), false);
});
