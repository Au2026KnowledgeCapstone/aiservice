# aiservice

The AI execution tier for BlindSpot. It receives a job from BlindSpotBE,
drives a browser (or an equivalent verification engine), and reports outcomes
back. It holds no product database, no user accounts, and no authorization
model — those belong to BlindSpotBE. The only storage this service owns is
operational: which jobs are in flight, and what has not yet been delivered
back to BE.

`ENGINE` picks which verification engine runs: one of `mvp`, `graph`, `stub`.
Unknown values fail at startup.

## Running

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

Copy `.env.example` to `.env` and adjust as needed.

## Service authentication

Set a nonempty `SERVICE_TOKEN` in `.env` and restart the API after changing it.
BlindSpotBE must send `Authorization: Bearer <SERVICE_TOKEN>` on
`POST /v1/jobs`. Missing or invalid credentials return `401` with a Bearer
challenge. An empty or whitespace-only configured token disables job intake
with `503`. Health checks at `/health` (and the `/heath` alias) remain public and report the
resolved engine name alongside status.

The job endpoint commits a new job before returning `202 Accepted` with
`{"job_id":"job-1","run_id":"run-1","duplicate":false,"status":"received"}`. Execution runs in
an `asyncio` background task; the HTTP response does not wait for the engine.
The returned status is the persisted state at response time (it can already be
`running` or `finished`). An identical retry returns `202` with `duplicate:true` and never starts a
second task. Reusing an ID with a changed stored envelope returns `409`.
Credentials are excluded from persistence and duplicate comparisons, so changing
only credentials does not rerun a job. Database failures return `503`.

`POST /v1/jobs/{job_id}/cancel` uses the same service token. It waits for the
local job task and browser cleanup before returning the terminal job status;
unknown job IDs return `404`. BE requests cancellation for accepted jobs and
uses the durable `job.cancelled` callback as the final run status.

Job envelopes require nonblank job/run IDs (up to 64 characters), a nonblank
callback token, HTTP(S) target and callback URLs, and positive integer viewport
dimensions. `engine` may be `mvp`, `graph`, `stub`, or omitted/null to use
`ENGINE`. `mvp` drives Chromium; `graph` is reserved and cannot execute yet. A job must
contain at least one requirement with a nonblank ID/title and at least one
criterion with a nonblank ID/text. Requirement descriptions may be empty.
Invalid envelopes return `422`.

## Running job intake

Apply migrations and set a nonempty `SERVICE_TOKEN` in `.env`. Install the
`engine` extra and Chromium for `ENGINE=mvp`, then start the API:

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
```

Use `/docs` to try authenticated requests, or submit a job from a shell where
`SERVICE_TOKEN` is set to the same value as the server:

```sh
curl -i http://127.0.0.1:8001/v1/jobs \
  -H "Authorization: Bearer ${SERVICE_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"job_id":"job-1","run_id":"run-1","target_url":"https://example.test","requirements":[{"id":"req-1","title":"Checkout","description":"Verify checkout","criteria":[{"id":"c1","text":"Checkout succeeds"}]}],"callback_base_url":"https://backend.test/internal/v1","callback_token":"example-callback-token"}'
```

`MAX_CONCURRENT_JOBS` limits execution per process. Waiting jobs remain
`received`; active jobs become `running`. Successful execution becomes
`finished`, including stub runs whose criterion verdicts are inconclusive.
Exceptions or `JOB_TIMEOUT_SECONDS` expiry produce `failed`; graceful shutdown
cancels queued and running tasks and records `cancelled`. Both session and engine
cleanup are attempted, with a five-second bound per close operation.

Evidence and requirement outcomes are retained in `outbox` (screenshots as
base64). Execution, metered usage, and cost-finalization callbacks are persisted
before delivery and sent by a leased, ordered outbox worker. Temporary failures
retry with exponential backoff; permanent rejections block later callbacks for
that job. See [the callback contract](docs/callback-contract.md). BlindSpotBE
implements authenticated callback ingestion. The stub-backend round trip and an
opt-in real BE HTTP round trip are tested without the frontend.

The metered provider writes a usage reservation before a model call and a final
usage revision afterward. Missing token counts/costs remain null. The MVP
browser engine and a metered provider are implemented; selecting `graph` still
produces a failed job.

Tasks are process-local. Abrupt process termination can leave `received` or
`running` records because credentials exist only in memory. Existing IDs are
never automatically rerun. After stopping all API workers, reconcile a stranded
job with `.venv/bin/python -m app.recovery JOB_ID`; it marks interrupted work
failed and unresolved usage unknown, then queues cost closure. It never reruns
a model request. Pending callbacks resume automatically after worker restart
and lease expiry; BE must deduplicate stable event IDs.
Run exactly one API worker: tasks and the concurrency semaphore are process-local.
The database primary key still prevents duplicate acceptance across workers, but
it cannot make the execution semaphore global. Do not use multiple uvicorn or
gunicorn workers for this A1 deployment.

## Operational database

Set `DATABASE_URL` in `.env` to a PostgreSQL database owned by this service,
then apply the schema:

```sh
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

The initial migration creates `jobs` and `outbox`; later migrations add metering,
PostgreSQL JSONB storage, and per-attempt callback sequencing. Run migrations before
starting the API; table creation is not performed automatically at startup.

For local development with PostgreSQL tools installed, an isolated cluster
can live in the ignored `.local/` directory:

```sh
initdb -D .local/postgres -A trust --no-locale -E UTF8
pg_ctl -D .local/postgres -l .local/postgres.log -o '-h 127.0.0.1 -p 55432 -k /tmp' start
psql -h 127.0.0.1 -p 55432 -d postgres -v ON_ERROR_STOP=1 -c "CREATE ROLE aiservice LOGIN PASSWORD 'aiservice' NOSUPERUSER NOCREATEDB NOCREATEROLE;" -c 'CREATE DATABASE aiservice OWNER aiservice;'
```

Use `DATABASE_URL=postgresql+psycopg2://aiservice:aiservice@127.0.0.1:55432/aiservice`
in `.env`. This cluster uses trust authentication for local development only.
Initialize the cluster and create the role/database once. On subsequent runs,
use the `pg_ctl ... start` command above. Stop it with:

```sh
pg_ctl -D .local/postgres stop
```

## Layout

```
app/
  main.py     # FastAPI endpoints and lifespan
  core/       # configuration, database access, service auth
  jobs/       # persistence, background runner, operational models
  schemas/    # validated job envelopes
  callbacks/  # wire schemas, encrypted tokens, sequencing, delivery worker
  provider.py # metered boundary, NullProvider, usage ledger, cost closure
  recovery.py # offline interrupted-job reconciliation
engines/
  base.py     # the Engine protocol — the contract every engine satisfies
  stub/       # deterministic engine; proves the round trip before a browser exists
  mvp/        # the browser engine: observation, action contracts, planning, assessment
  graph/      # reserved — crawl/graph engine, not implemented yet
```

An engine never touches the database, the filesystem, the network policy, or
an AI SDK directly — it only sees what `engines/base.py` hands it.

## The MVP browser engine

`ENGINE=mvp` drives a real Chromium browser (ported from
`BlindSpot/backend/app/services/browser/*`) through the same protocol the stub
engine satisfies: `prepare → survey → execute (per requirement) → close`.
Install the engine extra and Chromium before selecting it:

```sh
.venv/bin/pip install -e '.[test,engine]'
.venv/bin/python -m playwright install chromium
```

```sh
RUN_BROWSER_CAPABILITY_TEST=1 .venv/bin/python -m pytest -q
```

runs the opt-in real-browser suite (`tests/test_mvp_*.py`) alongside the rest.

Properties worth knowing before changing anything in `engines/mvp/`:

- **No configuration channel.** `JobEnvelope` carries only a target URL,
  viewport, a flat `credentials` dict, and requirements — there is no
  BrowserConfiguration equivalent (no service-origin allowlist, no
  write-allowlist, no objective checks). Login reuses
  `app/capabilities/verification.py`'s credential-key convention
  (`username`/`email`, `password`, optional `*_selector` overrides).
- **Read-only by default, and not configurable.** The AI planner always sends
  `allow_writes=False` — it never proposes a form submission or any
  business-data mutation. Criteria that inherently require a write come back
  inconclusive for that part.
- **Policy is target-origin-only**, plus unconditional metadata/link-local/
  RFC1918 blocking (loopback is the one exception, for local dev/test).
  Every request is re-checked twice: once via `context.route()` and again via
  the CDP `Fetch` domain, because `route()` alone only sees the first request
  of a redirect chain — confirmed directly, not assumed.
- **No pinned-IP egress proxy.** DNS-rebinding/TOCTOU protection (the
  reference's `proxy.py`) was deliberately not ported, matching
  `verification.py`'s own already-reviewed pattern.
- **The repeatability fingerprint is computed, never persisted** —
  `engines/mvp/repeatability.py`'s `fingerprint()` has nowhere to cache a
  plan; aiservice's operational store is job bookkeeping, not product state.

### Deployment constraints

- **Single process, `--workers 1`.** Jobs are scheduled in-process (A1); a
  second worker would duplicate browser runs and callback deliveries.
- **~2 GB memory, and raise `shm_size`** (`docker run --shm-size=1g` or the
  platform equivalent) or Chromium will crash in odd, hard-to-diagnose ways.
  The sandbox stays enabled — fix this with `shm_size`, not `--no-sandbox`.
- **A private service, reachable only from BE.** It should not be exposed on
  a public listener.

### Container

```sh
docker build -t aiservice .
docker run --shm-size=1g -p 8000:8000 --env-file .env aiservice
```

The image installs Chromium via `playwright install --with-deps` and runs as
a non-root user. There is no Xvfb/VNC in it — live browser viewing is not in
scope for this engine.

## Verification and handoff

```sh
.venv/bin/python -m pytest -q
# Optional real PostgreSQL test; creates/removes a disposable schema:
AISERVICE_TEST_POSTGRES=1 .venv/bin/python -m pytest tests/test_postgres.py -q
```

See [the completed A1 audit](docs/a1-compliance-audit.md),
[A1 implementation and environment reference](docs/a1-handoff.md),
[backend route ownership](docs/backend-route-inventory.md),
[A2 evidence proposal](docs/a2-evidence-design.md), and
[cost/RAG investigation](docs/ai-cost-and-rag.md). A2 cloud uploads, video,
retrieval, and real model integrations are intentionally outside A1.

## Backend capability extensions

This branch additionally implements authenticated document extraction, configured
browser login verification and job-bound evidence retrieval. These are explicit
extensions beyond A1. See [backend capabilities](docs/backend-capabilities.md)
for configuration, idempotency, private-network policy and opt-in browser tests.
The A1 stub engine remains unchanged; a configured login verifier is separate
from the future business-flow engine.
