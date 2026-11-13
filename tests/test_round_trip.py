import asyncio
from decimal import Decimal
import httpx
from sqlalchemy import select
from app.main import create_app
from app.jobs.models import Job, OutboxEntry
from app.provider import MeteredProvider, ProviderResponse
from engines.stub.engine import StubEngine
from tests.stub_backend import StubBackend
from tests.test_auth import PAYLOAD
from tests.test_intake import database, config, HEADERS


async def test_backend_dispatch_execution_usage_and_authenticated_callbacks(database):
    backend = StubBackend()
    backend.unavailable = 1
    calls = []
    class Transport:
        name = "fake"
        model = "fake"
        async def request(self, **kwargs):
            calls.append(kwargs["operation"])
            return ProviderResponse({"ok": True}, 10, 3, Decimal("0.001"))
    def engine_factory(name, provider):
        class Engine(StubEngine):
            async def execute(self, session, requirement, sink):
                await provider.structured(operation="judge", system="test", user="test", schema={})
                return await super().execute(session, requirement, sink)
        return Engine()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(backend.app)) as callbacks:
        app = create_app(
            config(callback_poll_seconds=0.01, callback_backoff_seconds=0.01), database,
            engine_factory, callback_client=callbacks,
            provider_factory=lambda sessions, job_id: MeteredProvider(sessions, job_id, Transport()),
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://aiservice") as dispatch:
                payload = {**PAYLOAD, "callback_base_url": "http://backend/internal/v1"}
                response = await dispatch.post("/v1/jobs", json=payload, headers=HEADERS)
                assert response.status_code == 202
                retry = await dispatch.post("/v1/jobs", json=payload, headers=HEADERS)
                assert retry.json()["duplicate"]
                await asyncio.wait_for(backend.finalized.wait(), 5)
        assert calls == ["judge"]
        assert backend.applied_events == 3
        assert len(backend.usage) == 1
        assert len(backend.finalizations) == 1
        assert next(iter(backend.usage.values())).amount == Decimal("0.001")
        assert next(iter(backend.usage.values())).revision == 2
        assert backend.deliveries == 7  # 3 execution, 2 usage, 1 closure, 1 retry
        with database() as db:
            assert db.get(Job, "job-1").status == "finished"
            callbacks_in_db = db.scalars(select(OutboxEntry).where(OutboxEntry.sequence.is_not(None))).all()
            assert all(row.delivered_at for row in callbacks_in_db)


async def test_stub_backend_rejects_wrong_token_and_deduplicates_charges():
    from app.callbacks.schemas import UsageEvent
    from app.jobs.models import now
    backend = StubBackend()
    payload = UsageEvent(
        usage_id="u1", event_id="e1", revision=2, cost_session_id="s1", job_id="job-1",
        provider="fake", model="fake", operation="judge", tokens={"input": 1, "output": 1},
        amount=Decimal("0.01"), status="succeeded", timestamps={"finalized_at": now()},
    ).model_dump(mode="json")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(backend.app), base_url="http://backend") as client:
        path = "/internal/v1/runs/run-1/ai-usage"
        assert (await client.post(path, json=payload)).status_code == 401
        headers = {"Authorization": "Bearer callback-token"}
        for _ in range(3):
            assert (await client.post(path, json=payload, headers=headers)).status_code == 200
        stale = {**payload, "event_id": "old", "revision": 1, "amount": None, "status": "reserved"}
        assert (await client.post(path, json=stale, headers=headers)).status_code == 200
    assert len(backend.usage) == 1
    assert sum(record.amount for record in backend.usage.values()) == Decimal("0.01")
