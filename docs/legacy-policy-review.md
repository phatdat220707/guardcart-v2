# Legacy Policy Engine review — 2026-11-17

Read-only source reviewed: `D:/guardcart-hacku2026/policy/policy_engine.py` and `test_policy_engine.py`. The legacy files were not modified or copied into v2.

## Baseline

The seven existing tests pass with their injected historical clock:

- HK$729 APPROVE.
- HK$770 ASK.
- HK$820 BLOCK.
- Untrusted merchant BLOCK.
- Expired mandate BLOCK.
- Points limit BLOCK.
- Wrong category BLOCK.

## Additional observations

Two direct function probes demonstrate input-validation gaps:

| Input | Observed legacy result | V2 requirement |
|---|---|---|
| `product.trusted = "false"` | APPROVE because a nonempty string is truthy | Reject string booleans at schema boundary; trust comes from the backend catalogue |
| `mandate.max_spend = float("nan")` | APPROVE because numeric comparisons do not reject NaN | Integer minor-unit limits; reject non-integer and non-finite numeric inputs |

These probes use the engine directly. They do not establish that a deployed public API accepts those inputs; upstream validation and the backend trust boundary must be checked separately.

Other code-level findings:

- Monetary values use float/round; v2 contracts specify integer HK cents.
- Several validators coerce values with float/int/bool instead of requiring correct input types. For example, int conversion can truncate fractional points.
- Automatic approval is always threshold-driven; v2 adds an explicit opt-in flag, default false.
- Timestamps without offsets are treated as UTC; v2 requires timezone-aware input.
- Rule evaluation accepts an injected clock, but audit timestamps use the wall clock independently. V2 should use one injected clock for reproducible tests and event ordering.
- Payment optimization ranks before spending-cap eligibility. An option with high rewards/high fees could be chosen and blocked even when another option is eligible. V2 ranks eligible options per product.
- The module evaluates policy; it does not implement stored quotes, session ownership, approval binding, one-time mandate consumption or atomic checkout. These belong in the new service/storage layers.

## Implementation direction

Reuse the policy concepts and expected outcomes, rather than copying the legacy engine unchanged. Implement explicit validated contracts, deterministic evaluation and separate transaction lifecycle handling.

Python backend validation will use Pydantic strict boolean/integer fields, forbidden extra input fields and explicit cross-field checks. Domain state changes remain separate from schema validation; having valid types does not itself grant authorization.

References:

- https://docs.pydantic.dev/latest/concepts/strict_mode/
- https://fastapi.tiangolo.com/tutorial/bigger-applications/

## Product language

All GuardCart v2 product UI, chat responses, validation messages and demo conversations are English. User input in other languages may receive an English clarification, but multilingual behavior is not a promised first-release feature. Internal collaboration/planning documents may remain Vietnamese.
