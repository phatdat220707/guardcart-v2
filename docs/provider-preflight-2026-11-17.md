# Zen API compatibility preflight — 2026-11-17

## Result

PASS for one authenticated, two-request Chat Completions tool round trip.

- Gateway: `https://zendigikey.shop/v1`
- Requested gateway model ID: `gpt-6-luna`
- Forced tool: `search_catalog`
- Local argument validation: passed (`category = running_shoes`).
- Tool result delivered to the model: one synthetic product P01.
- Final answer check: contained P01 and checkout total HK$729.
- Elapsed time for the successful pair of requests: 48.64 seconds.
- Output cap: 256 tokens per request; no automatic retries.

Before the successful run, one earlier attempt failed with a network/timeout error. Its processing and billing status are unknown. The successful run was performed outside the default sandbox; this alone does not establish the cause of the earlier timeout. Public status requests worked through both Python and PowerShell.

## Usage returned by the gateway

| Request | prompt_tokens | completion_tokens | total_tokens |
|---|---:|---:|---:|
| Tool selection | 1 | 20 | 21 |
| Tool-result response | 19 | 13 | 32 |

These are gateway-reported values, not independently verified token counts. A prompt count of 1 despite multiple messages and a tool schema suggests incomplete or nonstandard accounting. Do not treat these numbers as verified USD cost or use them for a precise spending guarantee. Account ledger reconciliation and provider pricing clarification remain necessary.

## Limits

- Verifies only this model ID, endpoint, configuration and sample at this time.
- Forced tool selection does not establish autonomous tool selection quality.
- Checking expected substrings is a minimal compatibility check, not a full answer evaluation.
- Does not verify strict JSON schema, streaming, model provenance, prompt-injection resistance, concurrency, billing accuracy or application readiness.
- A 48.64-second two-call round trip needs further latency benchmarking before model selection.
- No keys, raw request headers or raw provider error bodies are recorded here.

## Next checks

1. Reconcile the account ledger/balance, including the timed-out request if charged.
2. Build the provider-independent schemas/catalogue/policy baseline.
3. Later evaluate autonomous tool choice, structured responses and latency on a fixed test set before selecting the application model.
