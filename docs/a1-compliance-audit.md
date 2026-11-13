# A1 compliance audit — 2026-11-13

All seven A1 stages were reviewed against the supplied handoff and checked
against code and tests. This audit supersedes pre-integration statements that
BE has no typed callback schema or still returns 501. The user authorized
continuing through every checkpoint, fixing gaps, and committing/pushing major
changes; no PR or messages to other people were created.

Integration targets:

* aiservice `fix/a1-handoff-compliance`, based on `feat/JobIntake` at `5222a8f`.
* BlindSpotBE `feat/backend-ai-integration`, based on current FastAPI main.
  The older Flask checkout with local edits is preserved separately.

## Stage-by-stage verification

| Step | Requirement and evidence | Result |
| --- | --- | --- |
| 1 | `test_registry.py` launches a fresh interpreter. Registry tests: 4 passed. Adding an unrelated module importing a temporary `playwright` sentinel: 5 passed. No actual Playwright dependency installed or browser launched. | Pass |
| 2 | Realistic envelope, immutable snapshot mapper, missing callback token, unsafe URLs, positive viewports, and nonblank requirement/criterion IDs are covered by `test_job_envelope.py`. | Pass |
| 3 | Required own database URL, defaults 1/1800, missing-database construction failure, supported engines, and every field in `.env.example`: `test_config.py`. | Pass |
| 4 | PostgreSQL and SQLite upgrade/downgrade/upgrade and zero-drift checks; actual PostgreSQL JSONB types; credential stripping and encrypted callback secret checks. | Pass |
| 5 | Health reports resolved engine; authenticated 202 returns current persisted job status; two requirements finish in background; replay creates one row and executes each once; concurrent duplicates and execution semaphore verified. Start command explicitly uses `--workers 1`. | Pass |
| 6 | Same-transaction outbox, stable IDs, attempt-scoped sequence allocation, ordered delivery, PostgreSQL SKIP LOCKED, conditional leases, backoff, permanent-failure logs, and closure ordering verified. Two initial 503 responses recover. Actual SIGKILL mid-job leaves a durable running event; offline reconciliation and restarted delivery preserve its ID. | Pass |
| 7 | Fake transport observes reservation before request; final tokens/cost replace reservation; thrown/cancelled/abandoned calls retain unknown amounts; NullProvider raises. No OpenAI/Anthropic SDK added or called. | Pass |

Final aiservice command, with a **separate disposable aiservice PostgreSQL
DB**, a disposable test schema, and the matching BE checkout:

```sh
AISERVICE_TEST_POSTGRES=1 \
BLINDSPOT_BE_REPO=/absolute/path/to/BlindSpotBE-integration \
DATABASE_URL=postgresql+psycopg2://USER@127.0.0.1:PORT/aiservice_a1_audit_integration \
python -m pytest -q -p no:cacheprovider
```

Printed: **108 passed**, one existing Starlette/httpx deprecation warning.
No optional PostgreSQL or schema-comparison test was skipped in that run.

The BE suite was also run with `AISERVICE_REPO` pointing at this corrected
checkout and its interpreter supplied through `AISERVICE_PYTHON`: **79 passed,
25 subtests passed, 15 optional external checks skipped**. Its real HTTP test
starts actual BE and aiservice processes with separate databases, dispatches to
the stub, persists the inconclusive result, and verifies acknowledged cost
closure. The skipped BE checks require external infrastructure/PostgREST,
live Supabase privileges, or a separately checked-out API draft.

## Gaps corrected during this audit

* Health lacked the engine name; job intake lacked persisted state.
* README start command did not explicitly pin the service to one API worker.
* Callback claims had conditional leases but no PostgreSQL SKIP LOCKED.
* Permanent callback failures were recorded but not logged at ERROR level.
* PostgreSQL operational payloads were JSON rather than the specified JSONB.
* Callback schemas were permissive proposals rather than exact copies of the
  matching BE constraints. An opt-in subprocess test now compares schemas.
* Sequence uniqueness was keyed only by job. It now includes execution attempt;
  the atomic counter resets when the attempt increases and rejects backwards
  attempts. Delivery ordering also includes the execution attempt.
* The callback URL example differed between the handoff and implementation.
  Origin and already-prefixed forms now both target `/internal/v1/runs/...`.
* The existing retry test covered one initial 503 rather than two. It now covers
  two, and a new test kills the execution process mid-job before restarting
  delivery. Existing lease-crash coverage is retained.
* Historical docs still described BE callback ingestion as blocked. Current
  docs point to the implemented integration branch and distinguish the baseline.

## Endpoints

* `GET /health`: public status and resolved engine name.
* `GET /heath`: retained hidden compatibility alias.
* `POST /v1/jobs`: service Bearer authentication, validated envelope, durable
  idempotent admission, immediate 202 with job/run IDs, duplicate flag and state.
* FastAPI's `/docs`, `/redoc`, and `/openapi.json` documentation routes.

The receiver routes are BE's existing three POST endpoints, now implemented:
`/internal/v1/runs/{runid}/execution-events`, `/ai-usage`, and
`/ai-cost-finalizations`. aiservice has no product API routes or APIRouter.

## Decisions the handoff did not fully dictate

* Job IDs identify one immutable execution. A changed saved envelope is 409;
  replaying an identical job is 202. Secrets are excluded from comparisons.
  A1 executes attempt 1 once. Delivery retries do not increase execution attempt.
  The sequence allocator supports distinct attempt numbers, but automatic
  same-job execution retries and multi-attempt metering are not exposed in A1.
  BE reruns use a new run/job and reject higher attempts on an existing run.
* Both callback origin and `/internal/v1` prefix are accepted for compatibility.
* Callback tokens are encrypted separately so outbox delivery survives restart;
  raw browser credentials are omitted from every persisted envelope. A dedicated
  persistent key is recommended; a service-token-derived key supports local A1.
* Leases, failed-at timestamps, conditional acknowledgments, and stable event IDs
  provide at-least-once delivery. Exactly-once network delivery is not claimed.
* Cost closure verifies a count and SHA-256 digest. Usage has reserved/final
  revisions and exact decimal amounts; unknown stays null. These agreed wire
  fields extend the original prose example and match the BE implementation.
* Extra local evidence/outcome rows share the outbox table but have null sequence
  and are not delivered as callbacks. Actual private evidence storage is A2.
* Crashed jobs are reconciled offline, never silently rerun, because plaintext
  credentials are intentionally lost with the process.
* JSONB is used on PostgreSQL; JSON remains the SQLite test equivalent.
* No `app/api` package was added: all endpoints live in `app/main.py`, satisfying
  the handoff's explicit no-APIRouter / one-file route convention.

## Files added by A1

The inventory below includes the original A1 additions after A0 plus this audit.
Modified pre-existing files include README, Makefile, configuration, pyproject,
registry tests and existing engine-boundary tests. No real engine was added.

* `alembic.ini`
* `alembic/env.py`
* `alembic/script.py.mako`
* `alembic/versions/2c5c976b0eca_jobs_and_outbox.py`
* `alembic/versions/36fb0d13a104_callback_delivery.py`
* `alembic/versions/561ef0c991a2_metered_usage.py`
* `alembic/versions/a1c014_jsonb.py`
* `alembic/versions/a1c015_attempt_sequences.py`
* `app/callbacks/__init__.py`
* `app/callbacks/schemas.py`
* `app/callbacks/secrets.py`
* `app/callbacks/store.py`
* `app/callbacks/worker.py`
* `app/core/auth.py`
* `app/core/db.py`
* `app/jobs/__init__.py`
* `app/jobs/models.py`
* `app/jobs/runner.py`
* `app/jobs/store.py`
* `app/main.py`
* `app/provider.py`
* `app/recovery.py`
* `app/schemas/__init__.py`
* `app/schemas/jobs.py`
* `docs/a1-compliance-audit.md`
* `docs/a1-handoff.md`
* `docs/a2-evidence-design.md`
* `docs/ai-cost-and-rag.md`
* `docs/backend-route-inventory.md`
* `docs/callback-contract.md`
* `docs/callback-schemas.json`
* `tests/stub_backend.py`
* `tests/test_auth.py`
* `tests/test_be_contract.py`
* `tests/test_callback_delivery.py`
* `tests/test_callback_store.py`
* `tests/test_intake.py`
* `tests/test_job_envelope.py`
* `tests/test_jobs_store.py`
* `tests/test_migrations.py`
* `tests/test_postgres.py`
* `tests/test_provider.py`
* `tests/test_recovery.py`
* `tests/test_round_trip.py`
* `tools/prompt_metrics.py`

## Outside A1 / not claimed complete

A1's stub protocol checkpoints are complete. Browser engines, login verification,
document extraction, private evidence objects, signed downloads, real model
transports/pricing and remote cancellation belong to later handoffs. No cloud
resources, browser runs, real model requests, or production databases were used.
The referenced architecture/A0 handoff documents were not present in the local
checkouts; the supplied A1 text, existing engine seam and actual BE schemas were
the available audit sources. No external design agreement was fabricated.
