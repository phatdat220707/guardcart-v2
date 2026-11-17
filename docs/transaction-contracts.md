# GuardCart v2 — Transaction contracts and authorization rules

Status: domain schemas/policy, stored approvals, session-scoped access and atomic simulated checkout are implemented. FastAPI provides HTTP session/CSRF controls and serves the English workspace. The bounded Agent integrates with backend ranking and quotes; no approval or checkout tool is exposed to the LLM. Automated browser interaction, public deployment and real payments remain outside the verified release. See storage-and-checkout.md, http-api.md and verification-2026-11-17.md. Decisions confirmed by Dat on 2026-11-17.

## Confirmed product decisions

1. Every checkout requires human approval by default.
2. A user can explicitly enable automatic approval and set its threshold on the mandate confirmation form.
3. One mandate authorizes at most one successfully completed simulated purchase. A later purchase requires a new confirmed mandate.
4. Agent recommendations and tool outputs do not confer checkout authorization.

## Money and identity

- Currency: HKD only for the first release.
- Store monetary values as integer minor units (HK cents), never floating-point amounts. HK$800 is 80000.
- Prices/fees/rewards and limits are validated nonnegative; checkout totals must be positive.
- Backend-generated IDs and ownership checks apply to mandates, quotes, approvals and transactions.
- A browser session is the ownership boundary for the first release. An ID in a request is not sufficient proof of ownership.
- Client and model input cannot assign price, stock status, merchant trust or payment eligibility; the backend reads these from the controlled catalogue/payment data.

## Contract responsibilities

| Object | Essential fields | Authority |
|---|---|---|
| MandateDraft | category, max_spend_minor, preferences, missing_fields | Model may propose values; no authorization |
| ConfirmedMandate | id, session_id, version, category, max_spend_minor, auto_approve_enabled, approval_threshold_minor, max_points, trusted_merchants_only, expires_at, status | Backend validates user confirmation and stores a version |
| Product | id, version, category, merchant_id, price_minor, shipping_minor, available, attributes, description | Catalogue is the source of truth |
| Quote | id, session_id, mandate_id, mandate_version, product_id, product_version, payment_id, price_minor, shipping_minor, fee_minor, reward_minor, points_used, final_total_minor, effective_cost_minor, expires_at, status | Backend calculates and stores the exact proposal |
| PolicyDecision | id, quote_id, mandate_version, status, reasons, checks, evaluated_at | Deterministic backend evaluation |
| Approval | id, session_id, quote_id, mandate_id, mandate_version, decision, created_at | Explicit user action; grant applies only to that quote/version |
| Transaction | id, quote_id, mandate_id, authorization_type, status, idempotency_key, completed_at | Backend checkout service |
| AuditEvent | id, session_id, correlation_id, event_type, entity_id, timestamp, safe_details | Backend event recording |

All input contracts reject unexpected authority-bearing fields rather than silently accepting them. Dates are timezone-aware and evaluated using a server clock. Exact persistence field names can change before implementation, but authority boundaries must remain.

## Automatic approval is opt-in

Default confirmation fields:

```json
{
  "max_spend_minor": 80000,
  "auto_approve_enabled": false,
  "approval_threshold_minor": null
}
```

User-selected automatic approval example:

```json
{
  "max_spend_minor": 80000,
  "auto_approve_enabled": true,
  "approval_threshold_minor": 75000
}
```

Validation:

- `auto_approve_enabled` defaults to false.
- Disabled automatic approval requires a null threshold; enabling it requires a user-confirmed integer threshold between zero and max_spend_minor inclusive.
- Zero means every positive checkout still requires human approval.
- Expiry is exclusive: now >= expires_at is expired for both mandate and quote.
- A threshold extracted from chat is a suggestion shown in the form, not permission to enable automatic checkout.
- Confirmed mandate changes create a new version and invalidate outstanding quotes/approvals from the prior version. A consumed mandate cannot be reactivated by changing its version.

## Policy evaluation

Evaluate all applicable mandatory checks first. Return BLOCK for invalid/unavailable products, disallowed merchants/categories, expired or inactive mandates, invalid/stale quotes, exceeded maximum spend or exceeded points allowance. Validation failures may be represented as structured HTTP errors before evaluation; they never produce APPROVE.

Once mandatory checks pass:

1. If automatic approval is disabled, return ASK.
2. If final_total_minor exceeds the confirmed approval threshold, return ASK.
3. Otherwise return APPROVE.

Calculations:

```text
final_total_minor = price_minor + shipping_minor + fee_minor
effective_cost_minor = final_total_minor - reward_minor
```

Policy spending checks use final_total_minor. Reward value never expands either spending limit.

| Total | Auto approval | Threshold | Expected policy decision |
|---|---|---|---|
| HK$729 | Disabled | null | ASK |
| HK$770 | Disabled | null | ASK |
| HK$820 | Disabled | null | BLOCK |
| HK$729 | Enabled | HK$750 | APPROVE |
| HK$750 | Enabled | HK$750 | APPROVE |
| HK$750.01 | Enabled | HK$750 | ASK |
| HK$770 | Enabled | HK$750 | ASK |
| HK$800 | Enabled | HK$750 | ASK |
| HK$800.01 | Enabled | HK$750 | BLOCK |
| HK$820, reward HK$25 | Enabled | HK$750 | BLOCK despite effective cost HK$795 |

These examples assume a valid active mandate, in-stock eligible product, permitted merchant, max spend HK$800 and other mandatory checks passing. The original 729 APPROVE / 770 ASK / 820 BLOCK demo explicitly enables automatic approval at HK$750; it is not the default configuration.

## Decision versus user approval

- PolicyDecision remains an immutable record of evaluation. Human approval of an ASK result does not rewrite the original result to APPROVE.
- An allowed checkout records authorization_type = AUTO_POLICY or USER_APPROVAL.
- A user cannot approve away a BLOCK result or expand the mandate by clicking Approve.
- A declined quote is closed; the mandate remains available for a new proposal unless separately revoked, expired or consumed.
- Human approval is bound to exactly one stored quote and mandate version. Any quote component change requires a new quote, new evaluation and fresh human approval if ASK.
- Before completing checkout, the backend revalidates ownership, current price/availability, mandate/quote expiry and authorization. Even a lower changed price invalidates an existing quote; it is never silently substituted.

## One mandate, one successful purchase

Mandate lifecycle:

```text
DRAFT -> ACTIVE -> CONSUMED
             -> REVOKED
             -> SUPERSEDED
```

Expiry is checked from expires_at on every sensitive operation; an expired mandate cannot authorize checkout even if its stored status is ACTIVE.

Quote flow:

```text
CREATED -> EVALUATED -> AUTO_ALLOWED ---------> COMPLETED
                    -> AWAITING_APPROVAL -> APPROVED -> COMPLETED
                                        -> DECLINED
                    -> BLOCKED
```

Outstanding quotes may also become STALE or EXPIRED. Decline, BLOCK, quote expiry and search failures do not consume a mandate. The first successful checkout consumes it.

Checkout must atomically validate/consume the active mandate and record the completed simulated transaction. Two different quotes racing under the same mandate cannot both succeed. Idempotency alone is insufficient because the requests might use different keys.

- Repeating the same checkout with the same idempotency key returns the original outcome.
- Reusing that key with a different request returns a conflict.
- A second distinct purchase against a consumed mandate is rejected.
- The first release is simulation only; real payment reconciliation after external provider failures is outside this state machine's implementation scope.

## Acceptance tests to implement

1. A missing auto-approval setting results in ASK for an otherwise valid HK$729 purchase.
2. Chat/model output cannot enable automatic approval without the confirmation action.
3. The explicitly enabled 729/770/820 scenarios produce APPROVE/ASK/BLOCK.
4. Mandatory rule failure returns BLOCK even with human approval or rewards.
5. Threshold and budget boundaries are correct to one HK cent.
6. A mandate/quote belonging to another session is rejected.
7. An old approval cannot authorize a modified quote or mandate version.
8. Checkout rechecks stock, price and expiry after approval.
9. A successful checkout consumes the mandate; decline/failure does not.
10. Replayed checkout and racing distinct quotes cannot create two successful purchases from one mandate.
11. Audit records policy evaluation, approval/decline and simulated completion separately.

## Recommendation policy

Dat selected needs-fit first, then effective cost among suitable options. See recommendation-ranking.md for requirement/preference distinction, evidence rules, deterministic ordering and cases to evaluate.

## Open design decisions

- Final backend/frontend libraries and storage deployment.
- How much tool trace to show in the normal UI versus a developer/debug panel.
