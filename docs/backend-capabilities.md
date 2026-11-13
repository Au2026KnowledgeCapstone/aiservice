# Backend capability extensions

Branch `feat/backend-capabilities` extends the audited A1 protocol with document
extraction, configured credential-form verification, and operational evidence
retrieval. This work was explicitly authorized after the A1-only audit; A1's
job, callback, provider and stub-engine behavior remains compatible.

All new endpoints require `Authorization: Bearer <SERVICE_TOKEN>` and live in
`app/main.py`:

| Endpoint | Behavior |
| --- | --- |
| POST /v1/imports | Validate/normalize the required BRD/SRS plus optional design document, ask the configured provider for draft candidates, and attach original source passages |
| POST /v1/environments/verify | Check a configured login form with an isolated Chromium context and explicit observed success condition |
| GET /v1/jobs/{job_id}/evidence/{evidence_id} | Return bounded, hash-addressed content only when that operational evidence belongs to the specified job |

See `app/capabilities/schemas.py` for the complete wire contract. The optional
`BLINDSPOT_BE_REPO` contract test compares it exactly with
`BlindSpotBE/app/schemas/capabilities.py`. The matching backend branch is
`feat/backend-ai-integration`, PR #10. It implements the 17 assigned endpoints,
retains originals privately, and issues user-bound five-minute downloads.

## Setup

Apply `.venv/bin/alembic upgrade head` in aiservice's own operational database.
Never configure this service with BE's database URL.

Document extraction:

```text
EXTRACTION_API_URL=https://api.openai.com/v1
EXTRACTION_API_KEY=<provider credential>
EXTRACTION_MODEL=<explicit JSON-output-capable model>
EXTRACTION_TIMEOUT_SECONDS=80
```

No model is enabled by default. The compatible HTTP adapter uses JSON-object
output, bounded output, and `store=false`; its transport fields follow the
[official API reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
There are no automatic provider retries. Keep keys only in deployment secrets,
and select a model compatible with these fields. Real provider credentials and
paid model calls were not used during implementation/testing.

TXT, Markdown, selectable-text PDF and main-body DOCX are supported. The set is
limited to 10 MiB and 110,000 extracted characters; individual documents have
90,000-character limits. PDF parsing runs in a bounded subprocess; DOCX parsing
rejects unsafe XML/macros, encrypted archives and oversized packages. These
readers adapt v2's implementation. OCR, diagrams and arbitrary attachments are
not interpreted. Every candidate chooses a real passage ID; the server attaches
its original text and offsets. Human review is still required for model claims.

Browser verification:

```sh
.venv/bin/pip install -e '.[test,engine]'
.venv/bin/python -m playwright install chromium
```

```text
VERIFICATION_ENABLED=true
VERIFICATION_TIMEOUT_SECONDS=30
VERIFICATION_ALLOWED_PRIVATE_HOSTS=
VERIFICATION_BROWSER_EXECUTABLE=
```

The request carries `request_id`, `environment_id`, `revision`, `target_url` and
write-only `credentials`. Supported credential keys are `email` or `username`,
`password`, mandatory `success_selector`, and optional `username_selector`,
`password_selector`, `submit_selector`. Defaults target email/username inputs,
a password input and a submit button. The success selector must be absent or
hidden before submission and visible after it. Existing visible content is not
proof of authentication. Missing proof is inconclusive; incorrect login/timeouts
are rejected. SSO, MFA and captcha workflows are not automatically solved.

Each browser context blocks service workers, WebSockets and downloads. Network
requests must stay on the approved origin. Private targets require an explicit
host allowlist; metadata/link-local targets remain blocked. The environment's
credentials never go into an operation row, result, log, or model prompt.
`VERIFICATION_BROWSER_EXECUTABLE` can point at compatible system Chrome/Chromium;
otherwise use Playwright's Chromium. Run a single API worker; capabilities and
A1 execution share the process's concurrency semaphore.

## Durable operation semantics

`capability_operations` stores an ID, canonical input fingerprint, state and
sanitized result/error category. Identical completed operations replay without
another model/browser action; changed reuse is 409. Processing/failed/unknown
requests return 503 rather than automatically repeating potentially billable
work. Browser credentials are excluded from the saved fingerprint and never stored
as fields or payloads. Changing credentials alone does not rerun an operation;
use a new request ID for a new verification, as the backend does.

`capability_usage` records a reservation before the extraction request and a
final revision with reported token counts afterward. Cost remains null without
reported/configured pricing. Failed or interrupted requests remain unknown,
not zero. An operator must inspect external outcomes before intentionally
starting another operation; there is no automatic same-ID paid-call recovery.
These operational rows are separate from A1 per-run metering callbacks.

The evidence endpoint reads existing local outbox evidence and returns content,
media type, SHA-256 and capture timestamp when available. It does not expose
object paths or callback credentials and does not upload cloud objects. The
backend authorizes saved result citations before calling it, then caches the
verified content in its private persistent store. Legacy evidence without a
capture timestamp returns null rather than inventing one.

## Verification

Use a separate disposable PostgreSQL database and installed Chromium:

```sh
AISERVICE_TEST_POSTGRES=1 \
BLINDSPOT_BE_REPO=/absolute/path/to/BlindSpotBE-integration \
RUN_BROWSER_CAPABILITY_TEST=1 .venv/bin/python -m pytest -q
```

Tests cover reservations, idempotency, failures without repeated charges,
server-attached citations, unknown passage rejection, actual PDF/DOCX readers,
exact BE schemas, migration round trips/zero drift, and real browser success
and failure against a synthetic local login form. Backend integration tests run
this HTTP API with a fake model transport and real Chromium, and also retrieve
real A1 stub evidence through the service boundary. No outside application,
production database, paid model, or cloud infrastructure is used.

Recorded validation: **119 aiservice tests passed**, including real PostgreSQL
and browser opt-ins; the matching backend passed **93 tests and 25 subtests**,
with 15 optional external-infrastructure checks skipped. Focused tests confirm
browser credentials are excluded even from stored input fingerprints.
