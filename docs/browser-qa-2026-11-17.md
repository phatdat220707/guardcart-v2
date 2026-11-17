# Browser QA — 2026-11-17

Observed using the actual local workspace in the in-app browser and synthetic catalogue data. This is sampled interactive browser QA, not a checked-in end-to-end test suite or accessibility certification.

## Verified flows

| Case | Observed result |
|---|---|
| Budget zero | Form validation error; no confirmed mandate |
| Default mandate, HK$729 | ASK; automatic approval stayed off |
| Approval dialog | Correct product and exact HK$729 total |
| Cancel approval | No completed simulation at that point |
| Explicit simulated checkout | One receipt; consumed mandate |
| Reload after purchase | Same receipt and consumed state restored |
| Opt-in automatic threshold HK$750 | HK$729 APPROVE, HK$770 ASK, HK$820 BLOCK |
| Blocked quote | No approval or checkout control |
| Unconfirmed mandate edits | Product evaluation disabled |
| Threshold above revised budget | Form rejected inconsistent terms |
| Valid revised terms confirmed | Old selected quote cleared; new budget displayed |
| P01 search after repair | Exactly the P01 card returned |
| Reload during a real AI draft request | Saved request recovered as running; no repeated POST |
| Recover completed draft | Message and draft restored; session usage one provider attempt |
| Confirm draft | long_distance / road / EU42, high cushioning / light-weight preferences |
| Real recommendation loop | P02, P10, P01; all ASK; session usage three attempts |
| Reload recommendations | Stored results and selected quote restored, still three attempts |

Two synthetic purchases were completed during manual checks. The later AI workflow did not approve or execute another purchase. The provider UI check used one chat call and two recommendation calls. Billing, token usage and upstream model identity remain unverified; the separate 16-case benchmark was not rerun.

## Defects repaired

1. Completed purchases retained “Refresh quote” and could retain old expiry messaging. Completed views now hide both, including after reload.
2. Expired/invalidated quotes could display old ASK/APPROVE policy wording. Lifecycle state now takes precedence. Exact expiry boundaries, completion precedence, stale/declined states and receipt binding have JavaScript tests; expiry/stale transitions were not separately forced in the browser.
3. Catalogue search did not accept visible product IDs. It now searches IDs, names, purposes and brands.
4. Browser reload reused older unversioned JS while HTML changed. JS/modules/styles now require cache revalidation; an initial asset version tag invalidates earlier cached URLs. Browser recheck loaded the repaired ID search and receipt behavior. HTTP tests verify headers on both 200 and 304 responses.
5. Form help for a new draft over a consumed mandate was contradictory. It now explains that confirmation creates a new mandate while previous purchases remain recorded.

## Visual checks and limits

- Desktop viewport 1280 px; document width 1265 px.
- Mobile emulation 390 × 844 px; document width 375 px. No horizontal overflow observed. Approval dialog, receipt and bottom navigation were usable.
- Viewport override reset after mobile QA. Final desktop inspection returned no console errors.
- Local screenshots: `reports/browser-qa/desktop-workspace.jpg` and `reports/browser-qa/mobile-receipt.jpg`. These are ignored evidence files, not source-package assets.

The expanded pinned-environment suite passed 180 Python tests and 12 JavaScript tests. Frontend syntax and package-source inspection passed. Remaining validation: repeatable browser CI, keyboard/screen-reader audit, actual mobile devices, container execution and public HTTPS deployment.
