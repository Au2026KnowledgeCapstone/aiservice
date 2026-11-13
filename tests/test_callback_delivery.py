from datetime import timedelta
import httpx
import pytest
from sqlalchemy import select, update
from app.callbacks.secrets import token_cipher
from app.callbacks.store import execution_event
from app.callbacks.worker import DeliveryWorker
from app.jobs.models import OutboxEntry, now
from app.jobs.store import accept_job
from app.schemas.jobs import JobEnvelope
from tests.test_auth import PAYLOAD
from tests.test_intake import database, config


def seed(database, count=2):
    accept_job(database, JobEnvelope.model_validate(PAYLOAD), token_cipher(config()))
    with database.begin() as db:
        return [execution_event(db, "job-1", "job.running", {}).id for _ in range(count)]


def ready(database):
    with database.begin() as db:
        db.execute(update(OutboxEntry).values(next_attempt_at=None, lease_until=None))


async def test_retry_order_and_restart(database):
    ids = seed(database)
    received = []
    def backend(request):
        import json
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer callback-token"
        assert request.url.path == "/runs/run-1/execution-events"
        received.append(payload)
        return httpx.Response(503 if len(received) == 1 else 200)
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        worker = DeliveryWorker(config(), database, client)
        assert await worker.deliver_once()
        assert not await worker.deliver_once()  # retry blocks later sequence
        ready(database)
        worker = DeliveryWorker(config(), database, client)  # restart
        assert await worker.deliver_once()
        assert await worker.deliver_once()
    assert [r["event_id"] for r in received] == [ids[0], ids[0], ids[1]]
    with database() as db:
        assert db.get(OutboxEntry, ids[0]).attempts == 2
        assert all(db.get(OutboxEntry, key).delivered_at for key in ids)


@pytest.mark.parametrize("code,retry", [(408, True), (429, True), (500, True), (503, True), (400, False), (401, False), (403, False), (422, False), (302, False)])
async def test_http_classification(database, code, retry):
    ids = seed(database)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(code))) as client:
        worker = DeliveryWorker(config(), database, client)
        await worker.deliver_once()
        assert not await worker.deliver_once()
    with database() as db:
        row = db.get(OutboxEntry, ids[0])
        assert bool(row.next_attempt_at) == retry
        assert bool(row.failed_at) != retry
        assert row.last_error == f"HTTP {code}"
        assert not row.delivered_at


async def test_timeout_retries_without_sensitive_errors(database):
    ids = seed(database)
    def backend(request):
        raise httpx.ReadTimeout("secret token", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        await DeliveryWorker(config(), database, client).deliver_once()
    with database() as db:
        row = db.get(OutboxEntry, ids[0])
        assert row.last_error == "ReadTimeout"
        assert row.next_attempt_at


async def test_crash_after_backend_ack_reuses_event_id(database):
    ids = seed(database, 1)
    worker = DeliveryWorker(config(), database)
    entry = worker.claim()  # process dies after sending but before acknowledging
    assert entry["id"] == ids[0]
    restarted = DeliveryWorker(config(), database)
    assert restarted.claim() is None  # lease still active
    ready(database)  # simulate lease expiration
    claimed = restarted.claim()
    assert claimed["id"] == entry["id"]
    assert claimed["payload"] == entry["payload"]
    worker.acknowledge(entry)  # stale owner cannot overwrite recovered claim
    with database() as db:
        assert db.get(OutboxEntry, ids[0]).delivered_at is None
    restarted.acknowledge(claimed)


async def test_two_workers_cannot_claim_same_event(database):
    import asyncio
    seed(database, 1)
    workers = [DeliveryWorker(config(), database), DeliveryWorker(config(), database)]
    claims = await asyncio.gather(*(asyncio.to_thread(w.claim) for w in workers))
    assert sum(c is not None for c in claims) == 1


def test_process_exit_keeps_claim_and_message_for_recovery(database):
    import subprocess
    import sys
    ids = seed(database, 1)
    url = str(database.kw["bind"].url)
    script = '''
import os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.callbacks.worker import DeliveryWorker
from app.core.config import Settings
engine = create_engine(sys.argv[1])
worker = DeliveryWorker(Settings(database_url=sys.argv[1], service_token="secret"), sessionmaker(bind=engine))
assert worker.claim() is not None
os._exit(17)
'''
    process = subprocess.run([sys.executable, "-c", script, url], capture_output=True, timeout=10)
    assert process.returncode == 17, process.stderr.decode()
    worker = DeliveryWorker(config(), database)
    assert worker.claim() is None
    ready(database)
    entry = worker.claim()
    assert entry["id"] == ids[0]
    assert entry["attempts"] == 2


async def test_invalid_legacy_url_is_recorded_without_stopping_worker(database):
    ids = seed(database, 1)
    from app.jobs.models import Job
    with database.begin() as db:
        job = db.get(Job, "job-1")
        job.envelope = {**job.envelope, "callback_base_url": "http://bad\nhost"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        worker = DeliveryWorker(config(), database, client)
        assert await worker.deliver_once()
    with database() as db:
        row = db.get(OutboxEntry, ids[0])
        assert row.failed_at
        assert row.last_error == "InvalidCallbackURL"
