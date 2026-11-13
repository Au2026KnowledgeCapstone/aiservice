# A1 implementation handoff

Final checkpoint date: 2026-11-13. Branch: feat/JobIntake.
Scope confirmed by the user: aiservice only; skip contacting Vanshika/Nishitha;
commit and push each verified major checkpoint. No PR is opened.

## Delivered

| Workstream | Status and evidence |
|---|---|
| Job intake | Health, authenticated POST, strict envelope validation, durable acceptance, database idempotency, immediate 202, background execution, semaphore, lifecycle/cleanup, statuses and stub tests implemented |
| Callback persistence | Execution, usage and cost closure, unique stable IDs, atomic per-job sequence, encrypted callback token, transactional outbox implemented |
| Callback delivery | Background leased worker, retry/backoff, HTTP classification, attempt/failure tracking, ordered acknowledgment and restart recovery implemented |
| Metered provider | Durable reservations/final revisions, token/cost tracking, null unknown values, NullProvider/fake transports, no real SDK implemented |
| Integration | Stub BE dispatch → aiservice → authenticated callbacks/usage/closure tested, including outage and duplicate submissions |
| Real BE integration | Blocked: local BE dispatch and callback ingestion remain scaffolds/501; no BE modifications authorized |
| Original API parity | 37 operations inventoried and ownership assigned; product APIs remain BE work, real engine adapters remain engine work |
| Evidence/A2 | Storage/capture/upload/retrieval proposal documented; no cloud infrastructure or A2 runtime implementation |
| Cost/RAG | Original prompts/budget audited, static size measurements reproducible, optimization/retrieval proposal documented; no paid usage or measured savings |
| Coordination | Skipped at the user's explicit request; no messages sent and no agreements claimed |

## Local run and service configuration

1. Install `.venv/bin/pip install -e '.[test]'` after creating a Python 3.12 venv.
2. Start this repo's local PostgreSQL as documented in README; keep it separate
   from the BE product database. Set DATABASE_URL to this operational database.
3. Set ENGINE=stub and a nonempty SERVICE_TOKEN in .env. For production use a
   dedicated CALLBACK_ENCRYPTION_KEY (Fernet); keep it stable across restarts.
4. Run `.venv/bin/alembic upgrade head`, then `.venv/bin/alembic current`.
5. Start `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001`.
6. BE should use AI_SERVICE_URL=http://127.0.0.1:8001 and its AI_API_KEY must
   equal aiservice SERVICE_TOKEN. These are the names read by the local BE,
   but its placeholder handlers do not yet perform dispatch.
7. Each job uses callback_base_url such as http://127.0.0.1:8000/internal/v1
   and a fresh job-specific callback_token that BE persists/verifies. The
   service appends /runs/{run_id}/{callback-kind}; do not supply the full route.

The service consumes only DATABASE_URL for its own DB. It never needs BE DB
credentials. BE consumes HTTP endpoints and must not connect to this DB.
Use resolvable service hostnames rather than localhost between containers.
HTTPS and secret management belong in deployment configuration; no public
service, cloud bucket, external account, or real model request was created.

| aiservice variable | Default | Purpose |
|---|---|---|
| ENGINE | mvp | Select stub for A1; mvp/graph are not implemented |
| DATABASE_URL | required | Operational SQLAlchemy database URL |
| SERVICE_TOKEN | empty | Bearer token for intake; empty returns 503 |
| MAX_CONCURRENT_JOBS | 1 | Per-process execution semaphore |
| JOB_TIMEOUT_SECONDS | 1800 | Active execution timeout, excluding semaphore wait |
| CALLBACK_ENCRYPTION_KEY | empty | Fernet key; local fallback derived from SERVICE_TOKEN |
| CALLBACK_POLL_SECONDS | 1 | Idle outbox poll delay |
| CALLBACK_TIMEOUT_SECONDS | 10 | HTTPX request timeout |
| CALLBACK_LEASE_SECONDS | 30 | Claim duration; at least timeout + 5 seconds |
| CALLBACK_BACKOFF_SECONDS | 1 | Initial exponential retry delay |
| CALLBACK_BACKOFF_MAX_SECONDS | 300 | Maximum backoff, excluding longer Retry-After |

## Endpoints

| Endpoint | Authentication | Behavior |
|---|---|---|
| GET /health | Public | Liveness 200; does not promise database or BE readiness |
| GET /heath | Public, hidden alias | Same response, retained for original spelling |
| POST /v1/jobs | Service Bearer | Validate, commit, schedule once, 202 acknowledgment |
| GET /docs | Public | Generated interactive job API documentation |
| GET /openapi.json | Public | Generated FastAPI contract |

POST returns job_id, run_id, duplicate. Invalid input returns 422 without echoing
input/credentials; invalid auth returns 401; unconfigured auth or failed DB
write returns 503; ID reuse with a changed stored envelope returns 409.
Credentials and callback tokens are excluded from duplicate comparisons; changing
only those fields does not rerun an existing ID. Use a new ID for an intentional
new run. Omitted engine is resolved before comparison/persistence.

## Files added and changed

| Path | Responsibility |
|---|---|
| app/callbacks/schemas.py | Proposed typed execution, usage and closure payloads |
| app/callbacks/store.py | Transactional enqueue and atomic per-job sequence |
| app/callbacks/secrets.py | Callback-token encryption boundary |
| app/callbacks/worker.py | Leases, HTTP delivery, attempts, retries, acknowledgments |
| app/provider.py | Metered/Null providers, usage model, cost closure/digest |
| app/recovery.py | Offline failure/unknown reconciliation without rerunning calls |
| app/jobs/models.py | Operational callback sequence, ciphertext, lease and closure fields |
| app/jobs/store.py | Durable job intake, duplicate handling, secret stripping |
| app/jobs/runner.py | Engine lifecycle, status events, evidence/outcome retention, metering |
| app/main.py | HTTP auth/validation, 202 intake, runner and delivery lifespan |
| app/core/config.py and .env.example | Callback settings and operational defaults |
| alembic/versions/36fb0d13a104_callback_delivery.py | Callback/lease schema and legacy plaintext-token scrub |
| alembic/versions/561ef0c991a2_metered_usage.py | Usage ledger and cost-session closure |
| tools/prompt_metrics.py | Offline text/constant size comparison, no SDK/network |
| tests/test_callback_store.py | Sequence and encrypted-token checks |
| tests/test_callback_delivery.py | Retry, classification, lease, crash/restart checks |
| tests/test_provider.py | Reservation/final revision, unknown cost, cancellation, ordering |
| tests/stub_backend.py and tests/test_round_trip.py | Separate HTTP contract-test backend and complete round trip |
| tests/test_migrations.py | Upgrade/downgrade, legacy-token scrub, metadata comparison |
| tests/test_postgres.py | Optional disposable-schema PostgreSQL race tests |
| tests/test_recovery.py | Offline interrupted-job reconciliation |
| tests/test_auth.py and tests/test_intake.py | Authentication, credential-safe errors, intake/execution coverage |
| docs/callback-contract.md | Wire contract, retries, ordering and receiver requirements |
| docs/callback-schemas.json | Machine-readable proposed callback schemas for BE review |
| docs/backend-route-inventory.md | Original route inventory and BE/aiservice boundary |
| docs/a2-evidence-design.md | Deferred capture/storage proposal and cost assumptions |
| docs/ai-cost-and-rag.md | Source audit, measured prompt sizes and later evaluation plan |

## Verification recorded

- Main suite: 96 passed, 1 skipped (the optional PostgreSQL test).
- PostgreSQL opt-in: 1 passed against a temporary schema in the local aiservice DB.
- Final full run with PostgreSQL enabled: 97 passed.
- Full Alembic upgrade/downgrade/upgrade and metadata checks passed on SQLite;
  real PostgreSQL migrations and metadata checks passed too.
- New migrations applied to local PostgreSQL; head is 561ef0c991a2.
- Subprocess termination after claim preserves messages and attempts, redelivers
  after lease expiration, and cannot be acknowledged by a stale lease owner.
- Temporary BE failure retries the same event ID; callback token validation and
  usage-revision upserts prevent duplicate charges in the stub receiver.
- Existing architectural test confirms engines do not import app internals.
- One external Starlette TestClient/httpx deprecation warning remains; it does
  not fail tests and does not imply a tested migration to a new HTTP client.

## Operations and remaining boundaries

After an abrupt execution-process crash, stop all API workers before running:
`.venv/bin/python -m app.recovery JOB_ID`. It marks received/running work failed,
sets ProcessInterrupted, finalizes unresolved usage as unknown and enqueues
closure. Repeating it does not duplicate finalization. Restart the API and the
outbox resumes pending messages after any active lease expires. Live jobs are
not automatically resumed: browser credentials were intentionally not stored.

Permanent callback failures block the affected job's later messages. Inspect
outbox.failed_at/last_error, fix the contract/auth issue, then an operator may
clear failed_at and next_attempt_at on that exact event to replay the same ID.
Do not change payloads/event IDs to bypass an acknowledgment or missing-usage
error. Decryption failure requires the original key or deliberate redispatch;
no automatic key migration is implemented. Monitor undelivered age and failures.

Production BE must implement token/run binding, stable event-ID deduplication,
usage revision upserts, final count/digest validation, and durable 2xx responses.
Current 501 responses retry as 5xx; they are not evidence of integration success.
Actual browser behavior/parity, real AI pricing/model adapters, evidence object
uploads/downloads, cloud policies, frontend access, and RAG remain outside this
A1 branch. Single-process concurrency limits and in-memory task scheduling are
explicit; use one API worker for a service-wide execution cap.
