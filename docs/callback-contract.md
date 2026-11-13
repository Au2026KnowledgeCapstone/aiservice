# A1 callback contract and operational semantics

## Contract status

The wire schemas are synchronized with `BlindSpotBE/app/schemas/internal.py`
on BE `main`, which persists authenticated callbacks and verifies cost
count/digest. Run the optional schema-comparison test with `BLINDSPOT_BE_REPO`
pointing at the current BE checkout.
Machine-readable schemas are in [callback-schemas.json](callback-schemas.json).
See [the completed A1 audit](a1-compliance-audit.md) for current verification.

`callback_base_url` may be the BE origin (`https://be.example`), as in the
handoff, or an existing prefix (`https://be.example/internal/v1`). Both target
the same paths below; `/internal/v1` is appended exactly once. A reverse proxy
path can precede `/internal/v1`.

| Kind | POST path | Main fields |
|---|---|---|
| Execution | `/internal/v1/runs/run-1/execution-events` | event_id, job_id, attempt, sequence, type, occurred_at, schema_version, payload |
| Usage | `/internal/v1/runs/run-1/ai-usage` | usage_id, event_id, revision, cost_session_id, job_id, attempt, provider, model, operation, tokens, amount, currency, status, timestamps |
| Cost closure | `/internal/v1/runs/run-1/ai-cost-finalizations` | event_id, cost_session_id, revision, final_usage_count, final_usage_digest, closed_at |

Authentication is `Authorization: Bearer <job callback_token>`; this is distinct
from the global intake SERVICE_TOKEN. Redirects are not followed. A 2xx response
means durable acceptance, including an already-accepted duplicate. Response
bodies are not persisted or logged. BE must validate the token/job/run binding.

## Execution payloads

`job.running`, `job.finished`, `job.failed`, and `job.cancelled` carry status
and an optional error type. `requirement.completed` carries run_id,
requirement_id, and the engines/base.py outcome: verdict, classification,
observed_behavior, criterion_verdicts, and actions. Evidence IDs refer to local
outbox evidence records in A1. Retrieval and object references belong to A2;
these are not public download URLs. Stub verdicts are always inconclusive.

## Usage and cost closure

A usage_id identifies one attempted provider request. Revision 1 is `reserved`;
revision 2 is `succeeded`, `failed`, or `unknown`. Timestamps contain reserved_at
and, on a final revision, finalized_at. Tokens contain reserved_input (a text-byte
reservation plus framing), input, and output. Image tokens and measured tokens
remain unknown unless supplied by the transport. Reservations are not billed
usage. Amount is an exact decimal string or null, with currency USD by default;
unknown is never replaced by zero. Prompts, responses, and images are not saved
in the usage ledger. Engines may separately emit evidence.

Cost-session IDs are deterministic per job/attempt. Closure is revision 1 and
is queued after all terminal usage revisions; open reservations are converted
to unknown rather than presumed free. The final_usage_digest is SHA-256 of UTF-8
compact sorted-key JSON containing usage records sorted by usage_id. Each item
has usage_id, revision, status, tokens, amount (null or fixed 12-place decimal
string), and currency. final_usage_count counts requests, not callback revisions.
Closure means no more requests can be reserved; it does not turn unknown amounts
into known amounts. Even a stub job with zero model calls has a closure event.

BE should upsert usage by usage_id, accept only increasing revisions, and update
aggregates from the latest row, never add each retry as a new charge. It must
verify the final count/digest before closing the cost session. Test coverage
shows this behavior in a stub BE; production BE must implement it itself.

## Durability, ordering, and retries

Outbox insertion happens in the same transaction as the state/usage change.
Event IDs are created once and reused for every send. Sequence allocation uses
an atomic per-attempt database increment; the unique (job_id, attempt, sequence) constraint
prevents collisions. All callback kinds share this order; execution-event
sequence numbers can have gaps occupied by usage/finalization messages.

Delivery claims the earliest undelivered callback for each job using PostgreSQL
`FOR UPDATE SKIP LOCKED` plus a conditional lease
and conditional update. Only one lease owner can acknowledge it. No database
transaction is held while sending HTTP. Attempts increment before network I/O.
Process termination before acknowledgment results in redelivery after lease
expiry. Therefore delivery is at least once, not exactly once; stable event IDs
and BE deduplication are required. Other jobs may proceed independently.

HTTP 408, 429, 5xx and transport errors/timeouts retry with capped exponential
backoff. Numeric Retry-After seconds are honored (capped at one day); HTTP-date
Retry-After is not interpreted. HTTP 3xx and other 4xx are permanent failures,
recorded in failed_at/last_error; later callbacks for that job stay blocked.
Do not treat 409 as a generic duplicate acknowledgment: BE should return 2xx
for a valid replay. Backoff defaults to 1s, doubling to 300s. Attempts have no
maximum; delivery continues after restart. Operators should monitor stale
leases, failed_at, attempts, and next_attempt_at.

A network failure can happen after BE has committed. Resending the identical
event must have no additional product/cost effect. The sender cannot guarantee
this without the receiver implementing idempotency.

## Secrets and migrations

Browser credentials exist only in the in-memory task and are stripped from
stored envelopes. Job callback tokens are also stripped from envelopes and
stored encrypted in jobs.callback_secret. Set a stable dedicated Fernet
CALLBACK_ENCRYPTION_KEY in production. For local A1 only, an encryption key is
derived from SERVICE_TOKEN when no dedicated key is provided. Keep it stable
across restarts; changing the encryption key or that fallback token makes
pending ciphertext unreadable. No key-rotation workflow is implemented.

The first new migration removes legacy plaintext callback_token values. Old
jobs have no encrypted token and need a new dispatch/new job_id to authenticate
new callbacks. Legacy outbox evidence/outcomes have null sequence and are not
sent by the callback worker. Do not reclassify them as wire callbacks.

## Execution attempts versus delivery retries

Every A1 admitted immutable job runs once with execution `attempt=1`. Repeated
HTTP dispatches do not create another execution. An intentional BE rerun creates
a new run and job ID. The allocator and uniqueness constraint explicitly scope
sequences to `(job_id, attempt)`; increasing the execution attempt starts sequence
1, and moving backwards is rejected. The drainer orders by attempt then sequence.
Outbox `attempts` counts HTTP sends and never changes the execution attempt or
sequence. A higher-attempt producer is tested in isolation; there is no automatic
engine retry API or multi-attempt cost ledger in A1. BE explicitly rejects such
attempts on an existing run and requires a new run/job for a product rerun.
