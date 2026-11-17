# GuardCart v2

An English-language AI shopping workspace: describe a need, review an **unconfirmed draft**, confirm spending permissions, compare catalogue matches and approve an exact quote. A separate backend enforces authorization at simulated checkout.

Real LLM tool calls use a configurable Chat Completions gateway. Products, merchant flags, payments and rewards are synthetic. No real purchase is executed. The legacy hackathon repository is unchanged.

## Run locally

Python 3.11+. From this project directory in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -B -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open [the workspace](http://127.0.0.1:8000/) or [API docs](http://127.0.0.1:8000/docs). The same server serves the frontend; no Node build or frontend server is required. Keep the terminal running. If port 8000 is occupied, check whether GuardCart is already available there.

Without an AI key, confirm a mandate manually and use **Browse catalogue**. To enable AI, copy `.env.example` to a local `.env` without overwriting an existing configuration, then fill in the backend key and an account-supported model ID. Never commit that file. Process environment AI settings take priority. Set `GUARDCART_AI_ENABLED=false` to disable inference.

## Try the full workflow

1. Chat: “Road running shoes for long-distance training, EU42, under HK$800 including shipping. I prefer high cushioning and a light weight.”
2. Review the draft and confirm the mandate. Automatic approval stays off unless explicitly enabled in the form.
3. Select **Find matches with AI**. Mandatory requirements filter eligibility; preference matches precede effective-cost comparison.
4. Select a quote, inspect policy checks, then explicitly approve the exact total to complete a simulation.
5. Inspect the receipt and activity timeline. One completed purchase consumes the mandate; buying again needs a new confirmation.

If a response is lost, **Check previous AI request** reads a stored run/result without starting another model call. Reload also attempts this recovery. Failed runs require an intentional new request; there are no silent provider retries.

## Architecture and authority

```text
English workspace → FastAPI session / CSRF boundary
  Chat → bounded LLM extraction → unconfirmed draft
  User confirmation → stored, versioned mandate
  Agent → read-only catalogue tools → backend-ranked candidates
  Backend → versioned quote + deterministic policy decision
  Explicit approval when required → atomic simulated checkout → receipt / audit
                                 SQLite persistence
```

The LLM extracts requirements and participates in a bounded tool loop. It does not calculate authoritative totals, choose its own ranking criteria, confirm mandates, grant approval or execute checkout. `search_catalog` returns backend-ranked evidence; `get_product` reads previously returned candidates; `finish_recommendations` must preserve the canonical top-three order. This is intentionally a constrained Agent.

The backend checks strict integer HK-cent amounts, ownership, expiry, data versions, stock, category, trust, payment eligibility, budget, points and required fit. Rewards affect effective cost, not spending authority. Approval is quote-bound. Checkout revalidates current data and atomically consumes one mandate; unique constraints and idempotency prevent concurrent or repeated purchases from using it twice.

| Total, with HK$800 budget | Human approval by default | Explicit auto-approval up to HK$750 |
|---|---|---|
| HK$729 | ASK | APPROVE |
| HK$770 | ASK | ASK |
| HK$820 | BLOCK | BLOCK |

APPROVE alone does not execute a purchase. The separate checkout endpoint completes simulations only.

## Verification and evaluation

Offline checks do not load a real key or call a model:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe -B tools/evaluate_agent.py --validate-only
node --test frontend/tests/format.test.mjs frontend/tests/recovery.test.mjs frontend/tests/view-state.test.mjs
node --check frontend/dist/app.js
```

Node 24 is used for optional frontend checks. Python tests cover policy boundaries, persistence, rollback, separate-process races, session isolation, CSRF, Agent protocol enforcement, quotas, recovery and graders. Scripted providers verify application behavior; they do not measure model quality.

The versioned [dataset](evals/cases.json) contains 16 cases: 12 extraction/clarification cases and four recommendation/tool-protocol cases, including adversarial text. Live evaluation is opt-in:

```powershell
.\.venv\Scripts\python.exe -B tools/evaluate_agent.py --live --max-provider-calls 30
.\.venv\Scripts\python.exe -B tools/evaluate_agent.py --live --case draft-basic --max-provider-calls 1
```

Reports record hashes, per-case checks, failures, attempts and latency in ignored `reports/`. The runner never approves or checks out. See [evaluation methodology](docs/evaluation.md) and [provider preflight](docs/provider-preflight-2026-11-17.md). Dataset validation is not an evaluation score; a single run cannot establish prompt-injection immunity.

Latest local evidence: **182 Python tests in the pinned environment**, **12 JavaScript tests**, and sampled [desktop/mobile browser QA](docs/browser-qa-2026-11-17.md), including real AI request recovery. The separate application evaluation reached **16/16 after one size-handling repair**; its initial run was 15/16. Reports and limits are preserved in the [verification snapshot](docs/verification-2026-11-17.md). Browser QA used three additional provider attempts; it did not repeat the benchmark.

The private [GitHub repository](https://github.com/phatdat220707/guardcart-v2) runs Windows/Linux Python 3.11/3.14 checks and a Docker build/HTTP smoke job. See [current CI runs](https://github.com/phatdat220707/guardcart-v2/actions/workflows/ci.yml) and the [first-run findings](docs/github-ci.md). The initial Windows failure exposed OS-dependent JavaScript MIME types; the server now sets explicit asset types and tests that case.

Additional demos (use the same virtual environment):

```powershell
python tools/demo_policy.py
python tools/demo_policy.py --auto-approve
python -B tools/smoke_test_api.py
python -B tools/smoke_test_agent.py --live
```

The last two require the local server. The Agent smoke permits up to four metered calls; ordinary HTTP smoke uses no model and completes simulations only.

## Limits and provider controls

- Keys stay in the backend. The model ID is a gateway identifier; upstream model identity and billing are unverified.
- Persisted call reservations precede provider contact. Failed/time-out attempts count. Defaults: 200 attempts per database lifetime, 40 per session, 12 per session per minute, two simultaneous runs globally and one per session.
- Attempt limits are not a verified USD spending cap. Gateway token/credit reports are not assumed to equal dollars paid. Resetting the database resets local quotas.
- Anonymous sessions expire after 24 hours by default and have no account recovery. Browser storage contains workspace IDs; the bearer cookie is HttpOnly. Chat history is stored in local SQLite for context.
- SQLite is appropriate for this single-instance demo. Public hosting, production operations, account authentication and distributed throttling remain future work.
- Sampled browser flows passed at 1280 px desktop and 390 px mobile emulation. Repeatable browser CI, full accessibility, actual-device and public-host checks remain outstanding. The repository is private; no public v2 website URL exists yet.

## Source map

| Path | Responsibility |
|---|---|
| `backend/domain/` | Contracts, policy and evidence-based ranking |
| `backend/services/checkout.py` | Stored authority, approval and atomic checkout |
| `backend/ai/` | Provider adapter, bounded workflows and persisted call ledger |
| `backend/api.py` | Session, CSRF, Origin and public request boundary |
| `frontend/dist/` | English workspace served directly by FastAPI |
| `data/catalogue.json` | Controlled catalogue and adversarial fixtures |
| `tests/`, `frontend/tests/` | Offline regression checks |
| `evals/`, `tools/evaluate_agent.py` | Model-quality rubrics and live runner |

Runtime dependencies: Pydantic, FastAPI, Uvicorn and HTTPX. The frontend uses browser APIs without third-party runtime packages. Version ranges are in `requirements.txt`; dependencies' license terms continue to apply. Read [HTTP contracts](docs/http-api.md), [checkout design](docs/storage-and-checkout.md) and [ranking design](docs/recommendation-ranking.md).

## Deployment package

See [deployment preparation](docs/deployment.md) for the pinned runtime snapshot, portable entry point, Docker/Compose configuration, mounted AI key and source-only release command. The first GitHub container job built and passed HTTP smoke. A public host has not been selected; public HTTPS deployment remains unverified. Local source packaging does not publish the application.
