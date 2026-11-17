"""One contract, two sinks. No network or credential required."""
import base64
import hashlib

import pytest

from app.jobs.evidence_sink import GCSEvidenceSink, OutboxEvidenceSink


class FakeBlob:
    def __init__(self, objects, key):
        self._objects = objects
        self.name = key
        self.metadata = None

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        self._objects[self.name] = {"data": data, "metadata": dict(self.metadata or {}), "content_type": content_type}


class FailingBlob(FakeBlob):
    def upload_from_string(self, *args, **kwargs):
        raise RuntimeError("simulated upload failure")


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.fail_next = False

    def blob(self, key):
        if self.fail_next:
            self.fail_next = False
            return FailingBlob(self.objects, key)
        return FakeBlob(self.objects, key)


class FakeRunner:
    def __init__(self):
        self.entries: list[tuple[str, str, dict]] = []

    async def entry(self, job_id, kind, payload):
        entry_id = f"entry-{len(self.entries)}"
        self.entries.append((job_id, kind, payload))
        return entry_id


async def test_outbox_sink_embeds_base64_screenshot():
    runner = FakeRunner()
    sink = OutboxEvidenceSink(runner, "job-1")
    data = b"png bytes"
    evidence_id = await sink.put_screenshot(data, step_index=2)
    job_id, kind, payload = runner.entries[-1]
    assert evidence_id == "entry-0"  # unchanged: the outbox row's own id
    assert job_id == "job-1" and kind == "evidence"
    assert payload["kind"] == "screenshot" and payload["step_index"] == 2
    assert base64.b64decode(payload["base64"]) == data
    assert "storage_key" not in payload


async def test_outbox_sink_put_text_carries_plain_text():
    runner = FakeRunner()
    sink = OutboxEvidenceSink(runner, "job-1")
    evidence_id = await sink.put_text("hello", kind="log")
    _, _, payload = runner.entries[-1]
    assert evidence_id == "entry-0"
    assert payload == {"kind": "log", "text": "hello", "captured_at": payload["captured_at"]}


async def test_gcs_sink_writes_object_then_key_carrying_row():
    bucket, runner = FakeBucket(), FakeRunner()
    sink = GCSEvidenceSink(bucket, runner, "job-1")
    data = b"png bytes"
    evidence_id = await sink.put_screenshot(data, step_index=3)
    # The returned id is the object name, not the outbox row's id.
    assert evidence_id in bucket.objects
    stored = bucket.objects[evidence_id]
    assert stored["data"] == data
    assert stored["metadata"] == {"job_id": "job-1", "sha256": hashlib.sha256(data).hexdigest(),
        "media_type": "image/png", "captured_at": stored["metadata"]["captured_at"]}
    job_id, kind, payload = runner.entries[-1]
    assert job_id == "job-1" and kind == "evidence"
    assert payload == {"kind": "screenshot", "storage_key": evidence_id,
        "sha256": hashlib.sha256(data).hexdigest(), "media_type": "image/png",
        "captured_at": payload["captured_at"], "step_index": 3}
    assert "base64" not in payload


async def test_gcs_sink_maps_text_kind_like_operations_evidence():
    bucket, runner = FakeBucket(), FakeRunner()
    sink = GCSEvidenceSink(bucket, runner, "job-1")
    await sink.put_text("hello", kind="log")
    assert runner.entries[-1][2]["kind"] == "log"
    await sink.put_text("hello", kind="some-other-kind")
    assert runner.entries[-1][2]["kind"] == "page_text"
    _, _, payload = runner.entries[-1]
    assert "step_index" not in payload


async def test_gcs_sink_failed_upload_writes_no_outbox_row():
    bucket, runner = FakeBucket(), FakeRunner()
    bucket.fail_next = True  # forces the next blob's upload_from_string to raise
    sink = GCSEvidenceSink(bucket, runner, "job-1")
    with pytest.raises(RuntimeError):
        await sink.put_screenshot(b"png bytes")
    assert runner.entries == []
    assert bucket.objects == {}
