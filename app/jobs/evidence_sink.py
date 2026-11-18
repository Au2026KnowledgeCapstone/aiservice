"""Evidence leaves an engine through here; engines never choose a storage path.

OutboxEvidenceSink keeps evidence in the outbox as base64. GCSEvidenceSink
writes the object to the bucket first and only then writes a key-carrying
outbox row -- object before row, so a row never points at an object that was
never written. A crash between the two instead leaves an orphaned object,
which is harmless and sweepable.
"""
import asyncio
import base64
import hashlib
from uuid import uuid4

from google.cloud import storage

from app.jobs.models import now


class OutboxEvidenceSink:
    def __init__(self, runner, job_id):
        self.runner = runner
        self.job_id = job_id

    async def put_text(self, body, *, kind):
        return await self.runner.entry(self.job_id, "evidence", {"kind": kind, "text": body, "captured_at": now().isoformat()})

    async def put_screenshot(self, data, *, step_index=None):
        return await self.runner.entry(self.job_id, "evidence", {
            "kind": "screenshot", "step_index": step_index, "captured_at": now().isoformat(),
            "base64": base64.b64encode(data).decode("ascii"),
        })


class GCSEvidenceSink:
    """Evidence goes straight to the bucket; the outbox carries only the key.

    Kept deliberately even once nothing reads it (see the B6 handoff, step 7):
    it is the only record linking a job_id to the object keys that job
    produced, used to reconcile orphaned uploads against the bucket, and it
    keeps the cost/execution trail in one place. A row written for
    reconciliation is a design choice; stopping the write is a separate
    decision for a reviewer to make explicitly, not a side effect of nobody
    reading it.
    """

    def __init__(self, bucket, runner, job_id):
        self.bucket = bucket
        self.runner = runner
        self.job_id = job_id

    async def put_text(self, body, *, kind):
        mapped_kind = "log" if kind == "log" else "page_text"
        return await self._put(body.encode(), kind=mapped_kind, media_type="text/plain; charset=utf-8")

    async def put_screenshot(self, data, *, step_index=None):
        return await self._put(data, kind="screenshot", media_type="image/png", step_index=step_index)

    async def _put(self, data, *, kind, media_type, step_index=None):
        key = uuid4().hex
        sha256 = hashlib.sha256(data).hexdigest()
        captured_at = now().isoformat()
        await asyncio.to_thread(self._upload, key, data, media_type, sha256, captured_at)
        payload = {"kind": kind, "storage_key": key, "sha256": sha256, "media_type": media_type, "captured_at": captured_at}
        if step_index is not None or kind == "screenshot":
            payload["step_index"] = step_index
        # The object's key is the evidence id BE receives via cited_evidence_ids/
        # ActionRecord.evidence_id -- not the outbox row's own id.
        await self.runner.entry(self.job_id, "evidence", payload)
        return key

    def _upload(self, key, data, media_type, sha256, captured_at):
        blob = self.bucket.blob(key)
        blob.metadata = {"job_id": self.job_id, "sha256": sha256, "media_type": media_type, "captured_at": captured_at}
        # if_generation_match=0: the write fails rather than silently overwriting an existing key.
        blob.upload_from_string(data, content_type=media_type, if_generation_match=0)


def evidence_sink(config, runner, job_id):
    if config.evidence_sink == "gcs":
        if not config.gcs_evidence_bucket:
            raise ValueError("EVIDENCE_SINK=gcs requires GCS_EVIDENCE_BUCKET to be set.")
        bucket = storage.Client().bucket(config.gcs_evidence_bucket)
        return GCSEvidenceSink(bucket, runner, job_id)
    return OutboxEvidenceSink(runner, job_id)
