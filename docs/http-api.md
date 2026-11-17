# GuardCart v2 HTTP API

Implemented with FastAPI. Catalogue and checkout are simulated. The same server serves the English workspace at `/`; AI endpoints are enabled with backend provider configuration.

## Run locally

From the `guardcart-v2` root:

```powershell
python -m pip install -r requirements-dev.txt
python -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Use the project's virtual-environment Python path instead if following the root README setup.

- Interactive docs: http://127.0.0.1:8000/docs
- OpenAPI schema: http://127.0.0.1:8000/openapi.json
- Health: http://127.0.0.1:8000/api/health
- Persistent database: `var/guardcart.sqlite3` by default.

In a second terminal:

```powershell
python -B tools/smoke_test_api.py
```

This script creates a fresh anonymous session, explicitly enables the test mandate's HK$750 auto threshold, and exercises 729 APPROVE / 770 ASK / 820 BLOCK over real localhost HTTP. It simulates the user's approval for ASK and checks idempotent replay and one-time mandate consumption. It prints neither the bearer cookie nor the CSRF token. No external model/payment calls occur.

## Browser/API session protocol

1. Send `POST /api/session` with an allowed `Origin`. This sets an HttpOnly session cookie and returns `csrf_token`, internal session ID and server expiry. New sessions return 201; a still-valid existing session is reused with 200.
2. Retain cookies. Browser clients must use `credentials: "include"` when calling across local ports.
3. For state-changing requests, send both the exact allowed `Origin` and `X-CSRF-Token` from this session. The token is HMAC-derived from the unpredictable session bearer value and compared in constant time; a token from another session is invalid.
4. `GET /api/session` returns the current CSRF token without exposing the bearer. Session responses are not cacheable.
5. `DELETE /api/session` with those headers logs out, invalidates the bearer in storage and clears the cookie.

The server checks a fixed session lifetime from creation (24 hours by default); repeated bootstrap calls do not extend authorization lifetime. Session tokens are not account logins or recoverable identities. Expiry/logout loses access from that browser unless another valid session is retained; stored records remain for audit. This is an anonymous portfolio demo, not an account system.

Development defaults permit only loopback hostnames/origins. Use matching hostnames for frontend/backend, e.g. `127.0.0.1:5173` and `127.0.0.1:8000`, rather than mixing localhost and 127.0.0.1.

## Routes

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Public local health and simulation status |
| GET | `/api/catalogue` | Public synthetic catalogue: 20 products, 4 merchants, 4 payment options |
| POST / GET / DELETE | `/api/session` | Bootstrap/read/end the anonymous session |
| POST / GET | `/api/mandates` | Confirm/list owned mandates |
| GET | `/api/mandates/{id}` | Read an owned mandate |
| POST | `/api/mandates/{id}/reconfirm` | Confirm new terms/version and invalidate old quotes |
| POST | `/api/mandates/{id}/revoke` | Revoke an active mandate |
| POST | `/api/quotes` | Request a quote using mandate/product/payment IDs only |
| GET | `/api/quotes/{id}` | Read a stored quote and latest recorded policy |
| GET | `/api/quotes/{id}/decisions` | Read immutable policy history |
| POST | `/api/quotes/{id}/approval` | Explicit user approve/decline action |
| POST | `/api/quotes/{id}/checkout` | Complete a simulated checkout with `Idempotency-Key` |
| GET | `/api/transactions` | List the session's completed simulations |
| GET | `/api/transactions/{id}` | Read an owned completed simulation |
| GET | `/api/audit` | Read the session's audit timeline |
| POST | `/api/chat` | Extract an unconfirmed draft; metered when AI is enabled |
| GET | `/api/conversations/{id}` | Restore owned chat history and draft |
| POST | `/api/agent/recommend` | Bounded catalogue workflow; persist quotes, never buy |
| GET | `/api/agent/requests/{request_id}` | Recover run status using the client request ID |
| GET | `/api/agent/runs/{id}` | Read an owned run's status and call count |
| GET | `/api/agent/runs/{id}/result` | Read a completed stored result without inference |
| GET | `/api/agent/usage` | Read this session's attempt quota; billing is unverified |

No catalogue installation/admin mutation endpoint is registered. Human approval, mandate confirmation and checkout are not LLM tools. The Agent can call `propose_mandate` for extraction, or `search_catalog`, `get_product` and `finish_recommendations` for catalogue comparison.

## Request contracts

Quote creation accepts `mandate_id`, `product_id`, `payment_id` and optional integer `ttl_seconds` (1–3600). It rejects client-supplied prices, merchant trust, session IDs and policy decisions.

Approval accepts only `{"approve": true}` or `{"approve": false}`. Checkout accepts no body or `{}` and requires an alphanumeric/dash/underscore idempotency header (1–128 characters). Extra authority fields are rejected rather than ignored.

All amounts are integer HK cents. Strings standing in for booleans/integers, NaN, unsupported currency and timezone-less dates are rejected. Server identity and state are not part of the mandate confirmation input.

## Errors

Service errors use `{"error":{"code":"...","message":"..."}}`. Invalid request fields return 422 with field paths/types but not input values, validator contexts, cookies or stack traces.

- 401: missing, invalid, logged-out or expired session.
- 403: denied Origin/CSRF or an operation requiring valid policy/user approval.
- 404: missing or foreign-session resource; ownership is not revealed.
- 409: state/idempotency conflict.
- 422: invalid public request shape.
- 429: session creation/bootstrap throttle or persisted AI quota.
- 502: provider output failed the bounded tool protocol.
- 503: temporary storage lock contention, disabled AI or unavailable provider.

AI writes require a client-generated `request_id`. Identical completed requests return their stored result; changing input under the same key conflicts. A running request conflicts instead of starting another inference. Failed requests require an intentionally new ID. Recovery uses GET only, enforces ownership and never triggers inference. Runs with an expired four-minute lease are reported as FAILED. See [evaluation](evaluation.md) for provider measurements and accounting limitations.

HTTP framework errors and untrusted-host rejection may use the framework's standard response shape. The unexpected-error handler returns a generic 500 and logs the exception class without request bodies or credentials.

## Transport configuration

These settings are read from process environment, not the AI `.env` file:

| Variable | Default |
|---|---|
| `GUARDCART_ENVIRONMENT` | `development` |
| `GUARDCART_DATABASE_PATH` | `var/guardcart.sqlite3` relative to project root |
| `GUARDCART_ALLOWED_ORIGINS` | Exact loopback origins on ports 8000/5173 |
| `GUARDCART_ALLOWED_HOSTS` | `127.0.0.1,localhost` |
| `GUARDCART_SECURE_COOKIES` | false in development; true in production |
| `GUARDCART_SESSION_TTL_SECONDS` | 86400 |
| `GUARDCART_SESSION_CREATION_LIMIT` | 20 per minute per peer, per server process |

Production mode requires explicitly configured HTTPS origins and secure cookies. The secure cookie uses `__Host-guardcart_session`, Path=/, HttpOnly and SameSite=Lax with no Domain attribute. Origin/host wildcards are rejected. Non-secure cookies are restricted to local development configuration.

Only explicitly allowed credentialed CORS origins/headers/methods are permitted. API responses use no-store and nosniff. Origin checks protect state-changing routes, including initial session creation; CORS alone is not used as the CSRF control.

The session bootstrap throttle is in-memory and per-process, based on the server-observed peer rather than trusting arbitrary X-Forwarded-For headers. It is not a distributed quota, comprehensive abuse protection or AI budget control. Reverse-proxy trust/TLS, deployment volume configuration, full request/AI quotas and final browser verification remain deployment work.

## Verification

```powershell
python -B -m unittest discover -s tests -t .
```

HTTP tests use HTTPX ASGI transport with isolated real SQLite files. The separate smoke script verifies an actual TCP/HTTP server. Earlier service tests also cover two-process races and injected rollback.

References: https://fastapi.tiangolo.com/reference/security/ and https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html.
