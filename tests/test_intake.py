import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.db import Base
from app.jobs.models import Job, OutboxEntry
from app.main import create_app
from engines.stub.engine import StubEngine
from tests.test_auth import PAYLOAD

HEADERS = {"Authorization": "Bearer secret"}


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intake.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def config(**kwargs):
    return Settings(database_url="sqlite://", service_token="secret", engine="stub", **kwargs)


async def drain(app):
    await asyncio.gather(*tuple(app.state.runner.tasks))


async def test_stub_round_trip_and_duplicate(database):
    app = create_app(config(), database, start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            payload = {**PAYLOAD, "credentials": {"password": "never-persist"}}
            response = await client.post("/v1/jobs", json=payload, headers=HEADERS)
            assert response.status_code == 202
            await drain(app)
            duplicate = await client.post("/v1/jobs", json=payload, headers=HEADERS)
            assert duplicate.json()["duplicate"] is True
            conflict = await client.post("/v1/jobs", json={**payload, "run_id": "other"}, headers=HEADERS)
            assert conflict.status_code == 409
        with database() as db:
            job = db.get(Job, PAYLOAD["job_id"])
            assert job.status == "finished"
            assert job.started_at and job.finished_at
            assert job.envelope["credentials"] == {}
            entries = db.scalars(select(OutboxEntry).where(OutboxEntry.sequence.is_(None))).all()
            assert len(entries) == 2
            assert "never-persist" not in str(job.envelope)
            evidence = next(e for e in entries if e.kind == "evidence")
            outcome = next(e for e in entries if e.kind == "requirement_outcome")
            assert outcome.payload["outcome"]["criterion_verdicts"][0]["cited_evidence_ids"] == [evidence.id]


async def test_immediate_response_concurrency_and_simultaneous_duplicates(database):
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []
    class Blocking(StubEngine):
        async def execute(self, session, requirement, sink):
            calls.append(requirement.id)
            started.set()
            await release.wait()
            return await super().execute(session, requirement, sink)
    app = create_app(config(), database, lambda *args, **kwargs: Blocking(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            responses = await asyncio.wait_for(asyncio.gather(*[
                client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS) for _ in range(5)
            ]), 3)
            assert all(r.status_code == 202 for r in responses)
            assert sum(not r.json()["duplicate"] for r in responses) == 1
            await asyncio.wait_for(started.wait(), 2)
            second = await client.post("/v1/jobs", json={**PAYLOAD, "job_id": "job-2"}, headers=HEADERS)
            assert second.status_code == 202
            with database() as db:
                assert db.get(Job, "job-1").status == "running"
                assert db.get(Job, "job-2").status == "received"
            assert len(calls) == 1
            release.set()
            await drain(app)
            assert len(calls) == 2


@pytest.mark.parametrize("mode", ["failure", "timeout", "cancel"])
async def test_failure_timeout_and_shutdown_cleanup(database, mode):
    events = []
    entered = asyncio.Event()
    class Session:
        async def close(self):
            events.append("session.close")
    class Engine(StubEngine):
        async def prepare(self, target, credentials):
            events.append("prepare")
            return Session()
        async def survey(self, session, requirements):
            events.append("survey")
        async def execute(self, session, requirement, sink):
            events.append("execute")
            entered.set()
            if mode == "failure":
                raise RuntimeError("sensitive password")
            await asyncio.Event().wait()
        async def close(self):
            events.append("engine.close")
    app = create_app(config(job_timeout_seconds=1), database, lambda *args, **kwargs: Engine(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS)).status_code == 202
            await asyncio.wait_for(entered.wait(), 2)
            if mode != "cancel":
                await drain(app)
    with database() as db:
        job = db.get(Job, "job-1")
        assert job.status == ("cancelled" if mode == "cancel" else "failed")
        assert job.finished_at
        assert "sensitive" not in (job.error or "")
    assert events == ["prepare", "survey", "execute", "session.close", "engine.close"]


async def test_database_unavailable_returns_503(database):
    with database.kw["bind"].begin() as conn:
        Job.__table__.drop(conn)
    app = create_app(config(), database, start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            assert (await client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS)).status_code == 503
            assert not app.state.runner.tasks


async def test_prepare_failure_still_closes_engine(database):
    closed = []
    class Broken(StubEngine):
        async def prepare(self, target, credentials):
            raise RuntimeError("private detail")
        async def close(self):
            closed.append(True)
    app = create_app(config(), database, lambda *args, **kwargs: Broken(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            await client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS)
            await drain(app)
    assert closed == [True]
    with database() as db:
        assert db.get(Job, "job-1").status == "failed"


async def test_shutdown_cancels_running_and_waiting_jobs(database):
    started = asyncio.Event()
    class Blocking(StubEngine):
        async def execute(self, session, requirement, sink):
            started.set()
            await asyncio.Event().wait()
    app = create_app(config(), database, lambda *args, **kwargs: Blocking(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            await client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS)
            await started.wait()
            await client.post("/v1/jobs", json={**PAYLOAD, "job_id": "waiting"}, headers=HEADERS)
    with database() as db:
        assert db.get(Job, "job-1").status == "cancelled"
        assert db.get(Job, "waiting").status == "cancelled"
    assert not app.state.runner.tasks


async def test_engine_close_attempted_when_session_close_fails(database):
    closed = []
    class BrokenSession:
        async def close(self):
            raise RuntimeError("close failed")
    class Engine(StubEngine):
        async def prepare(self, target, credentials):
            return BrokenSession()
        async def close(self):
            closed.append(True)
    app = create_app(config(), database, lambda *args, **kwargs: Engine(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            await client.post("/v1/jobs", json=PAYLOAD, headers=HEADERS)
            await drain(app)
    assert closed == [True]
    with database() as db:
        assert db.get(Job, "job-1").status == "failed"


async def test_disconnect_during_commit_still_schedules_job(database, monkeypatch):
    import threading
    from app.jobs.store import accept_job
    from app.schemas.jobs import JobEnvelope

    committing = threading.Event()
    release = threading.Event()
    def delayed_accept(sessions, envelope, cipher=None):
        committing.set()
        if not release.wait(3):
            raise RuntimeError("Test did not release transaction")
        return accept_job(sessions, envelope, cipher)
    monkeypatch.setattr("app.jobs.runner.accept_job", delayed_accept)
    app = create_app(config(), database, start_delivery=False)
    async with app.router.lifespan_context(app):
        envelope = JobEnvelope.model_validate({**PAYLOAD, "engine": "stub"})
        request_task = asyncio.create_task(app.state.runner.admit(envelope))
        try:
            assert await asyncio.to_thread(committing.wait, 2)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
        finally:
            release.set()
        await asyncio.gather(*tuple(app.state.runner.admissions))
        await drain(app)
        with database() as db:
            assert db.get(Job, "job-1").status == "finished"


async def test_every_requirement_executes_after_single_survey(database):
    events = []
    class Recording(StubEngine):
        async def survey(self, session, requirements):
            events.append(("survey", len(requirements)))
        async def execute(self, session, requirement, sink):
            events.append(("execute", requirement.id))
            return await super().execute(session, requirement, sink)
    app = create_app(config(), database, lambda *args, **kwargs: Recording(), start_delivery=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            requirements = [*PAYLOAD["requirements"], {**PAYLOAD["requirements"][0], "id": "req-2"}]
            await client.post("/v1/jobs", json={**PAYLOAD, "requirements": requirements}, headers=HEADERS)
            await drain(app)
    assert events == [("survey", 2), ("execute", "req-1"), ("execute", "req-2")]
