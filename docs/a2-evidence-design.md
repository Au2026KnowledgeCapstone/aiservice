# A2 evidence storage proposal — not an A1 implementation

Research checked 2026-11-12. No bucket, cloud account, upload endpoint, video
recording, retention job, or cloud charge is created by this branch.

## Capture and transfer

Proposal: record the actual engine browser context to completed WebM files,
then upload after closing that context; retain a final screenshot if video
finalization fails. Playwright saves recordings on context closure, so capture
must be configured where the engine actually creates its context, not on an
unrelated outer page. Use fixed viewport/video size and monotonic action offsets.
This follows [Playwright video documentation](https://playwright.dev/python/docs/videos).

Capture screenshots before/after meaningful actions and immediately on an
assertion/locator failure, with a per-run count/size cap. Associate each with
run_id, job_id, requirement_id, event_id, step index, timestamp and checksum.
Separate evidence-upload failure from the requirement verdict.

Prefer completed uploads for initial A2. Live streaming adds transport/session
recovery and frontend work without changing requirement evaluation. For large
files, stream from disk via resumable upload; never load the entire recording
into RAM or encode it into a callback. A callback should carry only a stable
artifact ID, metadata, and object key. Retry upload completion with the same
artifact ID and checksum. Register metadata in BE only after verification.

## Options and proposed choice

| Option | Fit | Tradeoff |
|---|---|---|
| Google Cloud Storage | Aligns with the currently documented Cloud Run BE deployment; resumable upload and time-limited signed access | Region, IAM, and egress need configuration |
| Amazon S3 | Multipart upload and presigned object access | Adds cross-cloud configuration/egress if BE stays on GCP |
| Local filesystem | Useful engine-development fallback | Not durable/shared across container restarts; no production retrieval |

Proposed A2 default: a private GCS bucket colocated with the chosen BE deployment,
subject to team confirmation. GCS resumable sessions support interrupted large
uploads; session URIs must be treated as secrets. Signed URLs provide temporary
access, and lifecycle rules can enforce deletion. See [resumable uploads](https://docs.cloud.google.com/storage/docs/resumable-uploads),
[signed URLs](https://docs.cloud.google.com/storage/docs/access-control/signed-urls),
and [lifecycle rules](https://docs.cloud.google.com/storage/docs/lifecycle).
S3 remains an alternative with [multipart uploads](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)
and [presigned access](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

Proposed immutable key structure:
`v1/<tenant-id>/<project-id>/<run-id>/<job-id>/<artifact-id>.<webm|png>`.
Generate IDs server-side; never use page titles, usernames, supplied filenames,
or credentials as object paths. Store SHA-256, size, MIME type, duration/video
size, capture timestamps, and upload state alongside the BE artifact reference.

BE checks membership and run ownership before generating short-lived retrieval
URLs. Workers get write access to their assigned artifact prefix; browsers get
no bucket credentials. Use private buckets, service identities and upload size/
MIME/checksum validation. Upload tokens and signed URLs must not enter logs.

Proposed retention: 30 days for ordinary recordings/screenshots, configurable
longer retention for explicitly pinned runs, and prompt cleanup of failed uploads.
Deletion of a run must delete its artifacts and BE metadata. Retention values
are proposals requiring product approval; no deletion policy is deployed here.

## Cost estimate and acceptance work

Example workload assumption: 100 runs/day, 20 MB video + 5 MB screenshots/run,
30-day retention gives about 75 GB steady-state storage (decimal units).
At an illustrative storage rate r USD/GB-month, storage is approximately 75*r
USD/month, plus upload/read operations, egress, and any retrieval/replication
charges. For example r=0.02 would imply $1.50/month for storage alone; that is
an arithmetic example, not a provider price quote. Select the actual region,
class and network destination using [current GCS pricing](https://cloud.google.com/storage/pricing)
before committing infrastructure. Video length/bitrate and download frequency
can dominate the estimate.

A2 acceptance tests must upload/download a small PNG and a large WebM, interrupt
and resume transfer, verify checksum/metadata, reject unauthorized/cross-tenant
retrieval, expire a retrieval URL, preserve failure screenshots, test browser
close failure, delete a run, and exercise lifecycle cleanup. None of those
cloud tests are claimed as passing in A1. A1 evidence remains in local outbox
records; do not use that base64 prototype for production video storage.
