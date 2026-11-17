# Agent evaluation

## Distinguish the measurements

- Offline tests use scripted providers to exercise boundaries, failures, replay and persistence. Their pass count is not model accuracy.
- `--validate-only` checks dataset schemas, terms and product references. It makes zero calls, does not load `.env` and reports `model_quality_evaluated: false`.
- `--live` runs the actual provider through `AgentService` in an isolated temporary SQLite database. Each case gets a fresh session. Recommendation cases manually confirm synthetic terms as the harness's user action; the Agent never confirms them.
- Extraction cases check the application's extraction pipeline: exact HK cents, clarification, requirements, preferences, follow-up revisions and unconfirmed status. Backend normalization is included; this is not a score for raw model output alone. In particular, unresolved/non-EU sizes are cleared before returning a draft.
- Recommendation cases check read-only tool compliance and preservation of independently authored canonical IDs. Ranking is a backend responsibility; these cases do not measure an LLM's independent shopping judgement.

Cases include US/EU size ambiguity, unsupported goods/currency, missing budgets and prompt injection in user text and product descriptions. A bounded dataset cannot establish immunity to arbitrary attacks.

## Reproduce

From the project root with dependencies installed:

```bash
python -B tools/evaluate_agent.py --validate-only
python -B tools/evaluate_agent.py --live --max-provider-calls 30
python -B tools/evaluate_agent.py --live --case draft-size-ambiguity --max-provider-calls 1
```

Use the virtual environment's Python as described in README. `--live` explicitly permits metered calls. The default allowance is eight attempts; the maximum explicit allowance is 100. Cases without enough remaining allowance for their worst-case calls are SKIPPED. Failed and timed-out attempts count. There are no automatic retries; different runs can produce different outputs.

Ignored `reports/` files contain selected-case denominators, pass/fail/error/skip counts, per-field checks, separate backend invariants, attempts, latency, model ID and source/dataset hashes. The rubric is written before inference. Preserve baseline reports when changing prompts, and distinguish repair runs using hashes. Never replace an earlier failure with a success claim.

Billing and upstream model identity remain unverified. Locally observed calls and latency do not establish USD cost. The runner never prints credentials, cookies, raw provider responses or arbitrary exception messages. It never approves or checks out.

## CI

The included GitHub workflow runs offline Python tests, dataset validation, frontend checks and source-package inspection with read-only repository permission and no AI key. A separate job builds the Docker image and runs manual-only HTTP smoke. The workflow is prepared locally. GitHub execution, container runtime and the Linux/Python-version matrix are unverified until the separate v2 repository is created and the workflow actually runs.
