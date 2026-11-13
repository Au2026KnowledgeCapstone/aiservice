import asyncio
from decimal import Decimal
import httpx
import pytest
from sqlalchemy import select
from app.callbacks.secrets import token_cipher
from app.callbacks.worker import DeliveryWorker
from app.jobs.store import accept_job
from app.jobs.models import Job, OutboxEntry
from app.provider import MeteredProvider, NullProvider, ProviderResponse, UsageRecord, finalize_costs
from app.schemas.jobs import JobEnvelope
from tests.test_auth import PAYLOAD
from tests.test_intake import database, config


class FakeTransport:
    name = "test"
    model = "test-model"
    def __init__(self, response=None, error=None):
        self.response = response or ProviderResponse({"ok": True}, 12, 4, Decimal("0.001234"))
        self.error = error
        self.calls = 0
    async def request(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def setup(database, transport=None):
    accept_job(database, JobEnvelope.model_validate(PAYLOAD), token_cipher(config()))
    return MeteredProvider(database, "job-1", transport)


async def request(provider):
    return await provider.structured(operation="judge", system="secret system", user="secret prompt", schema={})


async def test_success_reserves_before_request_and_finalizes(database):
    class Transport(FakeTransport):
        async def request(self, **kwargs):
            with database() as db:
                row = db.scalar(select(UsageRecord))
                assert row.status == "reserved"
                assert row.tokens["input"] is None
                assert db.scalar(select(OutboxEntry)).kind == "ai_usage"
            return await super().request(**kwargs)
    provider = setup(database, Transport())
    assert await request(provider) == {"ok": True}
    with database.begin() as db:
        finalize_costs(db, "job-1")
    with database() as db:
        row = db.scalar(select(UsageRecord))
        assert row.status == "succeeded"
        assert row.revision == 2
        assert row.amount == Decimal("0.001234")
        assert row.tokens["input"] == 12
        events = db.scalars(select(OutboxEntry).order_by(OutboxEntry.sequence)).all()
        assert [e.kind for e in events] == ["ai_usage", "ai_usage", "cost_finalization"]
        assert [e.payload.get("revision") for e in events] == [1, 2, 1]
        assert events[-1].payload["final_usage_count"] == 1
        assert "secret prompt" not in str([e.payload for e in events])
    with pytest.raises(RuntimeError, match="closed"):
        await request(provider)


@pytest.mark.parametrize("failed", [True, False])
async def test_unknown_cost_is_not_zero(database, failed):
    transport = FakeTransport(error=RuntimeError("private") if failed else None,
                              response=ProviderResponse({"ok": True}))
    provider = setup(database, transport)
    if failed:
        with pytest.raises(RuntimeError):
            await request(provider)
    else:
        await request(provider)
    with database() as db:
        row = db.scalar(select(UsageRecord))
        assert row.amount is None
        assert row.tokens["input"] is None
        assert row.status == ("failed" if failed else "succeeded")
    assert transport.calls == 1


async def test_null_provider_and_abandoned_reservation(database):
    provider = setup(database)
    with pytest.raises(RuntimeError, match="No AI transport"):
        await request(provider)
    provider.reserve("abandoned", "judge", 42)
    with database.begin() as db:
        finalize_costs(db, "job-1")
    with database.begin() as db:
        finalize_costs(db, "job-1")  # idempotent closure
    with database() as db:
        assert db.get(UsageRecord, "abandoned").status == "unknown"
        finals = db.scalars(select(OutboxEntry).where(OutboxEntry.kind == "cost_finalization")).all()
        assert len(finals) == 1
        assert finals[0].payload["final_usage_count"] == 2
        assert db.get(Job, "job-1").costs_closed


async def test_cancelled_request_is_unknown(database):
    started = asyncio.Event()
    class Waiting(FakeTransport):
        async def request(self, **kwargs):
            started.set()
            await asyncio.Event().wait()
    provider = setup(database, Waiting())
    task = asyncio.create_task(request(provider))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with database() as db:
        row = db.scalar(select(UsageRecord))
        assert row.status == "unknown"
        assert row.amount is None


async def test_usage_delivery_precedes_cost_finalization(database):
    provider = setup(database, FakeTransport())
    await request(provider)
    with database.begin() as db:
        finalize_costs(db, "job-1")
    paths = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: paths.append(r.url.path) or httpx.Response(200))) as client:
        worker = DeliveryWorker(config(), database, client)
        while await worker.deliver_once():
            pass
    assert paths == ["/internal/v1/runs/run-1/ai-usage", "/internal/v1/runs/run-1/ai-usage", "/internal/v1/runs/run-1/ai-cost-finalizations"]
