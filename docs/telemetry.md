# AI service telemetry

Sentry project: `blindspot-aiservice` in the `blindspot-capstone` organization.
Use this service's DSN, not the frontend or BE DSN. No administrative token is
required at runtime. Keep deployment values in the team's secret manager.

## Configuration

`SENTRY_DSN` enables monitoring; blank disables initialization. Set
`SENTRY_ENVIRONMENT` to `production` only in production, and `SENTRY_RELEASE`
to the deployed commit SHA. `SENTRY_TRACES_SAMPLE_RATE` defaults to `0.1`
(10% of new traces); incoming sampling decisions can be inherited. Values
outside 0–1 fail configuration validation. Errors are captured separately
from performance sampling. Completion logs are emitted only for sampled requests.

`SENTRY_TRACE_PROPAGATION_TARGETS` is a JSON array of anchored regular
expressions. It defaults to `[]`: no outbound propagation to arbitrary browser
targets or model providers. For a trusted BE callback origin, for example:

```dotenv
SENTRY_TRACE_PROPAGATION_TARGETS=["^https://backend[.]example[.]com/"]
```

Configure BE's propagation targets to include the AI service origin as well.
This links incoming `sentry-trace`/`baggage` from BE to the AI request. The
service accepts a UUID `X-Request-ID` and replaces invalid values with a new
UUID, which is returned on ordinary HTTP responses. No CORS relaxation is
needed for server-to-server traffic. Existing bearer authentication stays in place.

## Coverage and privacy

Explicit FastAPI, Starlette, HTTPX and SQLAlchemy integrations capture request,
outgoing HTTP and local database timings. Job execution has a separate
`ai.job` span; caught job failures are explicitly reported without changing
their persisted status or retry behavior. This does not instrument BE internals
or a model provider's internal processing.

Only a fixed `Request completed` message is exported as a Sentry log, with
request ID, safe route, method, status and duration. Arbitrary application
logs are not forwarded. Span attributes are allowlisted. Exception messages,
request bodies/headers/cookies, query strings, SQL, local variables, user data,
and breadcrumbs are removed. Stack function/file/line metadata is retained.
Prompts, completions, screenshots, document contents, model keys, browser
credentials and callback tokens must never be added as telemetry attributes.
No automatic AI-provider, replay or profiling integrations are enabled.

## Verification and rollout

Install `.[test]` and run `pytest tests/test_telemetry.py tests/test_auth.py
tests/test_config.py`. The telemetry test uses an in-memory transport and mocked
HTTP calls: it verifies trace continuation, allowlisted propagation, redaction,
structured log/span export and health/auth behavior without a real provider call.

Deploy the reviewed code with the service's DSN and release metadata, then
check a health request and a controlled stub job in a staging environment.
Confirm the Sentry project/environment/release, trace linkage from BE, and
job failure event. Never add a public debug-error route to production. A local
synthetic verification is not proof that the deployed environment is configured.
To disable exports, clear `SENTRY_DSN` and restart the process.

SDK: `sentry-sdk>=2.75,<3`, tested with 2.75.0, matching the BE integration.
Official setup reference: https://docs.sentry.io/platforms/python/integrations/fastapi/
