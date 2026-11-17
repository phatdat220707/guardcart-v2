# Verification snapshot — 2026-11-17

Locally verified on Windows with Python 3.14.6 and Node 24.18.0. These are observed results, not a production-readiness certificate.

## Offline and HTTP

- 171 Python tests passed: `python -B -m unittest discover -s tests -t . -q`.
- Seven JavaScript tests passed: `node --test frontend/tests/format.test.mjs frontend/tests/recovery.test.mjs`.
- Frontend syntax check and 16-case dataset validation passed; dataset validation made zero provider calls.
- Actual localhost HTTP smoke passed: HK$729 APPROVE, HK$770 ASK, HK$820 BLOCK with explicit opt-in auto-approval up to HK$750. The smoke completed two simulations, no real payments and no AI requests. With automatic approval off, both valid totals require ASK.
- The running server returned healthy simulation/AI-enabled status and served the recovery module over HTTP 200.
- Lost-response recovery was checked through the API across an application restart with retained cookies and only a saved request ID. Retrieval did not make an additional provider call. This used a scripted provider and does not claim browser-network-failure automation.

## Live extraction and tool workflow evaluation

Requested gateway model ID: `gpt-6-luna`; provider: Zen API. Billing, reported token usage and upstream identity are unverified.

| Run | Cases passed | Failed / error / skipped | Provider attempts | Median per-case elapsed time |
|---|---|---|---|---|
| Initial baseline | 15 / 16 | 1 / 0 / 0 | 21 | 4,984 ms |
| After unresolved-size repair | 16 / 16 | 0 / 0 / 0 | 21 | 4,680 ms |

The single baseline failure was `draft-size-ambiguity`: clarification was requested, but the size field was not empty. Other authority invariants still passed. The repair explicitly instructs the model to leave unresolved EU size empty and independently clears non-EU or explicitly unresolved size output before returning the draft. Thus the second run measures the repaired application pipeline, including deterministic normalization; it is not raw-model accuracy.

Both runs used the same 16-case dataset, graders and catalogue. Their SHA-256 values and distinct Agent source hashes are preserved with every case outcome:

- [Initial report](evaluation-baseline-2026-11-17.json)
- [After-repair report](evaluation-after-size-fix-2026-11-17.json)

There were 42 observed provider attempts across these two evaluations, each separately limited to at most 30. Neither evaluation approved or executed a purchase. The extraction suite has 12 cases; four recommendation cases check tool protocol and preservation of backend-ranked product IDs, including no-match and adversarial-description cases. No additional repeats or model comparison were performed. Do not present this small dataset as universal accuracy or injection immunity; latency variation between two runs is not evidence of a speed improvement.

## Remaining validation

At the original snapshot, browser interaction, mobile accessibility, public deployment and GitHub CI were unverified. Subsequent browser checks are recorded below. Public hosting still requires persistent storage, HTTPS configuration and operational controls.

## Subsequent deployment-preparation checks

The pinned `requirements.lock` installed successfully from the public package index into a fresh project-local Python 3.14.6 virtual environment. `pip check` found no broken requirements. The expanded suite passed 179 Python tests and seven JavaScript tests. Eight new Python tests cover secret-file precedence/redaction, invalid configuration, release exclusions, credential-pattern rejection, integrity manifests and deterministic packaging. Dataset validation still makes zero provider calls. This phase did not rerun the model evaluation above.

Docker/Compose files, non-root execution, persistent data configuration and an optional mounted-key overlay are prepared. Docker is not installed on this development machine, so no container build/run result is claimed. See deployment.md for commands and the remaining host-specific work.

## Subsequent browser QA

[Interactive browser QA](browser-qa-2026-11-17.md) verified sampled desktop/mobile flows, quote-bound approval, receipt persistence, three policy outcomes and real AI request recovery after reloading mid-request. It used three additional provider attempts (one draft, two recommendation calls). Offline checks now pass 180 Python tests and 12 JavaScript tests. This is separate from the unchanged 16-case evaluation. Automated browser CI, complete accessibility, actual-device, Docker runtime and public HTTPS checks remain outstanding.
