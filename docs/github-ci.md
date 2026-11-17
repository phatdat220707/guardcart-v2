# GitHub repository and CI

Repository: [phatdat220707/guardcart-v2](https://github.com/phatdat220707/guardcart-v2), private. The initial local source commit `28f9932` was pushed to `main`; the remote file tree contained the 80 reviewed source files. `.env`, runtime database/session data, caches, reports and release archives were excluded. Existing repository visibility was preserved.

## Initial hosted run

[Run 44066004627](https://github.com/phatdat220707/guardcart-v2/actions/runs/44066004627), source commit `28f9932`:

| Job | Initial result |
|---|---|
| Linux, Python 3.11 | PASS |
| Linux, Python 3.14 | PASS |
| Docker build / Compose / actual HTTP smoke | PASS |
| Windows, Python 3.11 | FAIL |
| Windows, Python 3.14 | FAIL |

Both Windows failures occurred in the static-asset test: `/format.mjs` was served as `text/plain`, because Python/Starlette inherited that machine's MIME mapping. This would also prevent strict browser module loading with `nosniff`. The failure was not ignored or removed from the suite.

The server now sets explicit types for HTML, JS/MJS, CSS and SVG while keeping `nosniff` and cache revalidation. New regression tests simulate a wrong OS MIME mapping and verify a 206 range response retains both its JavaScript type and byte count. The local pinned suite increases from 180 to 182 Python tests; the JavaScript suite remains 12 tests.

Subsequent results are available in [workflow runs](https://github.com/phatdat220707/guardcart-v2/actions/workflows/ci.yml). A passing container job proves that this image can start and complete simulated HTTP workflows in the hosted runner. It does not establish a public HTTPS deployment, durable hosting storage or real payment integration.

CI uses no provider key and performs no paid model inference. The repository's anonymous application sessions and AI call ledger remain local runtime data; they were not uploaded with source.
