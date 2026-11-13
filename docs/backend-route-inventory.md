# Backend route ownership audit

Reviewed the local `BlindSpotBE/backend/main.py`, its OpenAPI document, and
`blindspottest/main.py` and semantic/runner modules on 2026-11-12.

The BE API is largely a scaffold. Legacy POST handlers echo input or return
placeholder success; newer product routes and internal callbacks return 501.
The original CLI has browser classification/execution functionality, but that
is not equivalent to an implemented product API. No claim of behavior parity
or real BE round-trip success can be made from these stubs.

The user confirmed that this change stays in aiservice. Therefore no BE files
are modified, and product routes are not duplicated in the executor.

| Existing operation | Responsibility |
|---|---|
| `GET /` | BE health |
| `POST /api/create-project` | BE product API |
| `POST /api/dashboard` | BE product API |
| `GET /api/dashboard` | BE product API |
| `POST /api/accounts` | BE product API |
| `POST /api/environments` | BE product API |
| `GET /api/environments` | BE product API |
| `POST /api/projects/{project_id}` | BE product API |
| `POST /api/projects/{project_id}/flows/{flow_id}` | BE product API |
| `GET /api/projects/{project_id}/flows/{flow_id}` | BE product API |
| `POST /api/projects/{project_id}/run-test` | BE product API |
| `GET /api/projects` | BE product API |
| `GET /api/environments/{environment_id}` | BE product API |
| `GET /api/projects/{project_id}/credential-references` | BE product API |
| `GET /api/projects/{project_id}/execution-policies` | BE product API |
| `GET /api/projects/{project_id}/flows` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}` | BE product API |
| `POST /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/reruns` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/steps` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/steps/{step_id}/actions` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/failures` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/artifacts` | BE product API |
| `POST /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/artifacts/{artifact_id}/download` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/logs` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/ai-usage` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/regressions` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/comments` | BE product API |
| `POST /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/comments` | BE product API |
| `GET /api/notifications` | BE product API |
| `GET /api/notifications/unread-count` | BE product API |
| `PATCH /api/notifications/{notification_id}` | BE product API |
| `GET /api/projects/{project_id}/environments/{environment_id}/runs/{run_id}/events` | BE product API |
| `GET /api/events` | BE product API |
| `POST /internal/v1/runs/{run_id}/execution-events` | BE callback ingestion; aiservice sends |
| `POST /internal/v1/runs/{run_id}/ai-usage` | BE callback ingestion; aiservice sends |
| `POST /internal/v1/runs/{run_id}/ai-cost-finalizations` | BE callback ingestion; aiservice sends |

## What stays where

BE owns project/environment/flow CRUD, user auth and ownership, analysis-run
creation, immutable requirement snapshots, run numbering, credential references,
job dispatch retries, product results, cost aggregation, notifications, and
artifact access authorization. Legacy POST query routes can be retired after
BE callers migrate to the corresponding resource GET routes; actual removal
requires a caller inventory and is not done in this branch.

aiservice owns authenticated job intake, operational idempotency, bounded
execution, the engine/provider boundary, operational evidence, metering,
and durable callback delivery. Requirements already reach the selected engine
through immutable snapshots; requirement outcomes return as execution callbacks.

The intended run/start/rerun BE handlers must save the product run and dispatch
record together, then send a stable job_id and job-scoped callback token to
POST /v1/jobs. They must retry using that same job_id. They must never import
this service's models or access its database. aiservice never reads BE's DB.

## Original engine preservation

The original CLI's page classifier inspects a page snapshot; its flow classifier
uses an application graph projection and readable elements. The flow runner
performs deterministic navigation, checks contracts, and saves local reports.
Porting that engine requires a dedicated adapter implementing prepare/survey/
execute/close and mapping its outcomes and evidence to engines/base.py. Copying
its product-like routes or SDK calls into intake would bypass the intended
architecture. MVP/graph adapters remain separate engine work; A1 validates the
end-to-end protocol with the stub engine and a fake model transport only.

A parity test suite needs concrete original CLI inputs, expected outcomes,
and the chosen engine adapter. Those fixtures and real adapters are not part
of A1; this branch does not claim to recreate the browser runner.
