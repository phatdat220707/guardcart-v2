# Stored authorization and simulated checkout

Implemented in `backend/storage.py` and `backend/services/checkout.py`.

## Session boundary

`create_session()` generates an unpredictable bearer token. Only a SHA-256 digest is stored; the raw token is returned once to the caller. Every user-facing service method resolves that token to an internal session ID and restricts record access to that owner. Nonexistent and other-session records both return `not_found`.

The HTTP layer is implemented in backend/api.py: HttpOnly cookies, session-bound CSRF, exact Origin/CORS/host checks, server-enforced session expiry and logout. Anonymous sessions expire 24 hours after creation by default. Session bootstrap throttling is per peer/process, not comprehensive public/AI rate limiting. Actual browser frontend verification and deployment controls remain pending. See http-api.md. The service never takes a model-supplied session ID as proof of identity.

`install_catalogue()` is a trusted admin/bootstrap operation. It is not an Agent tool or a user endpoint. Catalogue updates require an increasing version when any stored data changes. Readers receive validated backend data, not client-provided prices or trust fields.

## Persistent data

SQLite stores sessions, versioned catalogue records, confirmed mandates, quotes, append-only policy decisions, per-quote approval decisions, completed simulated transactions and append-only audit events. Mandate and quote records retain current status; historical policy decisions are never rewritten by a user approval.

Session/audit/transaction data is scoped to its owner. Database files and their WAL sidecars are excluded by `.gitignore`. Audit records omit session tokens and the AI provider key. Append-only here describes application behavior, not a tamper-proof audit log against an administrator who can edit the database.

## Confirm, quote and approval

- Mandate confirmation accepts only the public terms schema. Server IDs, state and versions are generated internally.
- Automatic approval is disabled unless explicitly enabled in those confirmed terms.
- Quote creation accepts mandate, product and payment IDs, reads the catalogue under a transaction, calculates totals and stores a policy decision.
- An ASK quote remains pending until an explicit user approval/decline action. Approval actions are not available to an LLM tool.
- Approval refers to the exact quote ID and mandate version. Decline closes that quote but leaves the mandate available.
- Reconfirmation increments the mandate version and invalidates open quotes. A consumed mandate cannot be reconfirmed; a new purchase requires a new mandate.
- Before approval and checkout, policy is evaluated again using current stored catalogue records and the service clock.
- A changed price, fee, reward, stock, eligibility, version or mandate invalidates the original authorization when relevant. A changed quote is never silently substituted, even for a lower price.
- `get_quote()` returns a persisted snapshot and latest recorded policy, not a live catalogue refresh. Approval/checkout perform the decisive fresh checks.

## Atomic checkout

Checkout runs inside `BEGIN IMMEDIATE`. The writer lock is acquired before reading authorization state; the injected clock is read after lock acquisition. Catalogue writes use the same database, so an admin update cannot interleave between policy revalidation and transaction completion.

Within one transaction:

1. Authenticate the session.
2. Resolve a successful idempotency replay before changing state.
3. Load the owned quote/mandate and current catalogue records.
4. Reevaluate the complete policy; require an exact approval if ASK.
5. Conditionally change ACTIVE mandate to CONSUMED at the expected version.
6. Insert one completed simulated transaction.
7. Complete that quote, invalidate other pending quotes for the mandate and append audit events.
8. Commit all changes together.

Database constraints independently enforce one completed transaction per mandate, one per quote and one per `(session_id, idempotency_key)`. Writer serialization, conditional consumption and unique constraints cover different idempotency keys and different quotes racing for the same mandate.

Any unexpected exception rolls back all intermediate changes, including policy/audit inserts in that checkout attempt. Expected policy denials commit the fresh failed policy/audit record, then raise a structured service error. They do not consume the mandate or reserve a successful idempotency result.

## Idempotency

- Same session, key and quote: return the exact original completed transaction, even if the mandate has since expired. This is a response replay, not a new purchase.
- Same session/key, different quote: `idempotency_conflict`.
- New key against an already consumed mandate: reject.
- A denied attempt creates no transaction/idempotency reservation; after supplying the missing valid approval, the caller can retry that key.
- Identical keys in different sessions do not grant access across sessions.

## Verification

The offline suite includes actual SQLite persistence across new service instances, injected rollback failure, two threads with separate database connections, two processes using independent services, duplicate-key races, changed catalogue records, expired quotes/mandates, revoked/reconfirmed mandates, foreign-session access and immutable ASK history.

This release performs simulated checkout only. It does not implement real payment intents, bank integration or recovery/reconciliation with an external payment processor.

References: https://www.sqlite.org/lang_transaction.html and https://docs.python.org/3/library/sqlite3.html.
