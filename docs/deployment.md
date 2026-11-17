# Deployment preparation

This release targets one persistent Python service. No public v2 hosting account/service has been created or deployed. Docker is not installed on the development PC. The [first GitHub CI run](https://github.com/phatdat220707/guardcart-v2/actions/runs/44066004627) successfully built the image and passed the container HTTP smoke job; its Windows jobs separately exposed a MIME-type issue. Read [CI findings](github-ci.md) and [current runs](https://github.com/phatdat220707/guardcart-v2/actions/workflows/ci.yml) for subsequent verification.

## Repeatable Python setup

`requirements.txt` documents compatible ranges; `requirements.lock` pins the runtime snapshot. It includes platform markers for Windows-only dependencies. It is a version snapshot, not a hash-verified supply-chain lock or a frozen base image digest.

Windows PowerShell, from project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -B -m backend.server --check
.\.venv\Scripts\python.exe -B -m backend.server
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -B -m backend.server --check
.venv/bin/python -B -m backend.server
```

The entry point uses one worker, loopback by default and `PORT` (default 8000). `--check` validates configuration without inference or database creation; it does not test provider availability, filesystem permissions or deployment connectivity. Transport configuration comes from process environment; the AI loader also supports the local ignored `.env`.

## Local Docker run

After installing a Docker engine with Compose and Linux-container support, from project root:

```bash
docker compose -f deploy/compose.yaml up --build --detach --wait
```

Open http://127.0.0.1:8000. The default container is manual-only, publishes only on loopback, runs as UID/GID 10001, drops capabilities, uses a read-only root filesystem and writes SQLite to the named `guardcart-data` volume at `/data`. The healthcheck sends the configured allowed Host header. It is a liveness check, not proof that future database writes or inference will succeed.

If the existing native app occupies port 8000, retain it and choose a separate local port. PowerShell:

```powershell
$env:GUARDCART_LOCAL_PORT = "8002"
docker compose -f deploy/compose.yaml up --build --detach --wait
```

Open http://127.0.0.1:8002. The Compose configuration updates its allowed browser origins to match. The container still listens on port 8000 internally. On Linux/macOS prefix the command with `GUARDCART_LOCAL_PORT=8002` instead.

`docker compose -f deploy/compose.yaml stop` pauses the service. The named volume holds conversations, sessions, mandates, receipts and the AI attempt ledger across container replacement. Do not use an ephemeral filesystem for a public instance. Volume removal or rollback can lose receipts and reset quota history.

### Optional AI secret mount

The image does not include `.env` or keys. If enabling AI locally, create `deploy/secrets/ai-key.txt` in your editor with the key alone, on one line; this directory is ignored. Then run:

```bash
docker compose -f deploy/compose.yaml -f deploy/compose.ai.yaml up --build --detach --wait
```

The overlay mounts the key at `/run/secrets/guardcart_ai_key` and enables AI deliberately. The file must be readable by the non-root container user; on Linux configure appropriate host ownership/group permissions. Compose file-backed secrets do not automatically repair host file permissions. No key is passed as a build argument or committed source. Docker documents [Compose secret mounts](https://docs.docker.com/compose/how-tos/use-secrets/) and why [build secrets must not be baked into ARG/ENV](https://docs.docker.com/reference/build-checks/secrets-used-in-arg-or-env/).

`GUARDCART_AI_API_KEY_FILE` accepts a single nonempty UTF-8 key (up to 16 KiB). An explicit process-level file setting overrides a local `.env` key. An explicit direct process key overrides a file setting from `.env`. Configuring both direct and file process settings is rejected. Mounted-file errors are redacted. The key is loaded on startup; rotate it by updating the runtime secret and restarting the service.

## Public host requirements

Use `deploy/production.env.example` as a template in the chosen host's runtime settings. It is not a deploy command and the example domain is not an actual v2 URL.

1. One running instance and one worker with a durable writable `/data` directory owned by UID/GID 10001. Keep SQLite's database, WAL and shared-memory files together. See [Docker volume lifecycle](https://docs.docker.com/engine/storage/volumes/).
2. HTTPS termination at the host/reverse proxy, exact `GUARDCART_ALLOWED_ORIGINS` and `GUARDCART_ALLOWED_HOSTS` matching the final domain, and `GUARDCART_ENVIRONMENT=production`. Secure host-prefixed HttpOnly cookies are required.
3. A process/container command of `python -B -m backend.server --host 0.0.0.0`; `PORT` must match the host's private service port. Route frontend and API through the same origin.
4. Inject a runtime key or mounted key file only after the manual flow works. Keep AI disabled initially. Attempt caps do not establish a verified USD cap; the anonymous demo shares the global allowance across visitors.
5. Keep the raw service port behind the HTTPS proxy. The entry point does not trust forwarded headers. Per-peer throttling may consequently share a proxy address; configure edge limits at the selected host. Distributed sessions/throttling, public-account authentication and real payments are not implemented.

Verify health, session cookies, explicit Origin/CSRF, the three policy scenarios, quote-bound approval, replay and volume persistence on the final HTTPS domain before sharing it. Production HTTPS verification is still outstanding.

## Source-only release

```bash
python -B tools/package_release.py --check
python -B tools/package_release.py
```

The tool includes allowlisted source directories, reviewed templates and an integrity manifest. It excludes designated key/runtime paths, databases, ZIPs, installed dependencies and caches, rejects source links and checks known credential patterns without printing matching values. This heuristic does not prove arbitrary text is free of every possible secret. New source directories/types require an explicit allowlist change.

The ZIP goes into ignored `releases/` and contains `guardcart-v2/` plus `release-manifest.json`. Its name uses a source-content digest. Installation and a new user-supplied configuration are still required after extraction; the package is not a running deployment.
