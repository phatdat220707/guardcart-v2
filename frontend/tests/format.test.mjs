import test from "node:test";
import assert from "node:assert/strict";
import { parseMoney, moneyInput, money, localDateTime } from "../dist/format.mjs";

test("money input is exact to the cent, including policy boundaries", () => {
  for (const [value, expected] of [["729",72900],["750.00",75000],["750.01",75001],["800.01",80001],["0",0],["1.1",110]]) assert.equal(parseMoney(value),expected);
});
test("exponents, fractional cents, NaN and negative amounts are rejected", () => {
  for (const value of ["", "-1", "NaN", "Infinity", "1e2", "750.001", "1,000.00", "10000000000.01"]) assert.throws(() => parseMoney(value));
});
test("integer money values round-trip through the editable display", () => {
  for (const value of [0,1,72900,75001,80001,1_000_000_000_000]) assert.equal(parseMoney(moneyInput(value)), value);
  assert.equal(money(72900),"HK$729.00");
  assert.equal(money(-500),"-HK$5.00");
});
test("local expiry input preserves the same UTC minute", () => {
  const instant = "2026-11-17T09:00:00.000Z";
  assert.equal(new Date(localDateTime(instant)).toISOString(),instant);
  assert.throws(() => localDateTime("invalid"));
});
