# Render Free deployment — 2026-11-19

Live demo: https://guardcart-v2.onrender.com/

The public demo is manual-only: confirm a mandate with the form, browse the controlled catalogue, evaluate a quote and explicitly approve simulated checkout. AI chat/recommendation is disabled and no provider key was uploaded. Real LLM integration remains available in the local application; this hosted demo does not claim live AI inference.

## Observed deployment

- Render Free Docker web service in Singapore, one instance, selected compute price US$0/month.
- No persistent disk or paid upgrade. Service ID `srv-dbvhftrm8hqs73a0824g`.
- Initial deployed source commit `737231dca70bfeaf2c01fb2b9b4871c02b533d49`, after all five hosted CI jobs passed.
- Initial deploy ID `dep-dbvhfujm8hqs73a084o0`; dashboard reported “Deploy succeeded | Live”.
- HTTPS production settings, Secure cookies, port 10000, health path `/api/health`, auto-deploy after CI passes. The exact origin/host derives from validated Render metadata.
- SQLite at `/data/guardcart.sqlite3` uses the container's ephemeral filesystem, not a persistent volume.

## Public browser verification

The actual HTTPS site opened with an active anonymous session and AI off. Form confirmation, catalogue search by product ID and quote creation succeeded.

1. With automatic approval off, HK$729 required explicit approval. The approval dialog showed the exact total; one simulated checkout completed and consumed the mandate.
2. Reloading in the same running instance restored the same receipt. This does not establish persistence across restart or sleep.
3. With user-enabled automatic approval up to HK$750, the three cases displayed HK$729 APPROVE, HK$770 ASK and HK$820 BLOCK. The blocked quote exposed neither approval nor checkout actions.
4. No JavaScript console errors were returned by the final inspection. One synthetic purchase was executed, no real payment and no model call.

Evidence screenshots are stored locally in ignored `reports/render/`: `free-plan-live.jpg` and `public-free-block.jpg`.

## Free-demo limits

Render Free sleeps after inactivity and can take time to wake. Files, anonymous sessions, receipts and quota records can disappear on sleep, restart or redeploy. See [Render's free-service limits](https://render.com/docs/free). The current demo therefore keeps AI disabled; an ephemeral local attempt ledger is not a durable spending control. Enabling paid inference publicly requires a separate cost-control decision.

Compute was selected at US$0/month and no paid disk was attached. Free-plan usage limits and account billing rules still apply; this report is not a guarantee against every possible future charge or configuration change. The repository remains private, while the deployed demo URL is public.
