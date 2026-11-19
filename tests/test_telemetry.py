import json
from uuid import UUID, uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sentry_sdk.transport import Transport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, _settings_from_sources
from app.core.telemetry import configure_telemetry, scrub_event, scrub_log
from app.main import create_app


def test_disabled_without_dsn(monkeypatch):
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: pytest.fail("unexpected init"))
    assert configure_telemetry(Settings(database_url="sqlite://")) is False


def test_configuration_validation():
    for rate in (-1, 2, float("nan")):
        with pytest.raises(ValidationError):
            Settings(database_url="sqlite://", sentry_traces_sample_rate=rate)
    config = _settings_from_sources({"DATABASE_URL": "sqlite://", "SENTRY_TRACE_PROPAGATION_TARGETS": '["^https://backend[.]test/"]'})
    assert config.sentry_trace_propagation_targets == ["^https://backend[.]test/"]


def test_privacy_filters():
    event = scrub_event({"user": {"email": "sensitive"}, "extra": {"prompt": "sensitive"},
        "request": {"url": "https://target.test/v1/jobs/sensitive/cancel?token=sensitive", "data": "sensitive", "headers": {"authorization": "sensitive"}},
        "exception": {"values": [{"type": "RuntimeError", "value": "sensitive", "stacktrace": {"frames": [{"function": "run", "vars": {"password": "sensitive"}}]}}]},
        "spans": [{"op": "db", "description": "sensitive", "data": {"query": "sensitive"}}]})
    assert "sensitive" not in json.dumps(event)
    assert event["request"]["url"] == "/v1/jobs/{id}/cancel"
    assert scrub_log({"body": "sensitive"}) is None


@pytest.mark.asyncio
async def test_background_failure_is_captured_without_changing_job_status(monkeypatch):
    from app.jobs.runner import JobRunner
    runner = JobRunner(Settings(database_url="sqlite://", sentry_dsn="https://public@localhost/1"), None)
    runner.status = AsyncMock()
    failure = RuntimeError("private engine details")
    runner.execute = AsyncMock(side_effect=failure)
    captured = []
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda exc: captured.append(exc))
    await runner.run(SimpleNamespace(job_id="synthetic-job"))
    assert captured == [failure]
    assert runner.status.await_args.args == ("synthetic-job", "failed", "RuntimeError")


def test_real_service_continues_trace_and_scrubs_payloads(monkeypatch):
    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    original_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: original_init(**kw, transport=MemoryTransport))
    config = Settings(database_url="sqlite://", engine="stub", sentry_dsn="https://public@localhost/1",
                      sentry_traces_sample_rate=1, sentry_trace_propagation_targets=[r"^https://backend\.test/"])
    engine = create_engine("sqlite://")
    app = create_app(config, sessionmaker(bind=engine), start_delivery=False)
    seen = {}

    @app.post("/v1/jobs/{job_id}/cancel-test")
    async def synthetic_failure(job_id: str):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: (seen.update({req.url.host: dict(req.headers)}), httpx.Response(200))[1])) as client:
            await client.post("https://backend.test/callback", json={"password": "sensitive"})
            await client.post("https://target.test/private?token=sensitive")
        raise RuntimeError("sensitive")

    trace_id = "a" * 32
    request_id = str(uuid4())
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/v1/jobs/sensitive/cancel-test?token=sensitive", json={"prompt": "sensitive"}, headers={
                "sentry-trace": f"{trace_id}-{'b' * 16}-1", "X-Request-ID": request_id, "Authorization": "Bearer sensitive"})
            assert response.status_code == 500
            assert seen["backend.test"]["sentry-trace"].startswith(trace_id)
            assert "sentry-trace" not in seen["target.test"]
            response = client.get("/health", headers={"X-Request-ID": "sensitive"})
            UUID(response.headers["X-Request-ID"])
            assert response.status_code == 200
        sentry_sdk.flush()
        payloads = [item.payload.json for envelope in envelopes for item in envelope.items if item.payload.json]
        assert payloads
        assert "sensitive" not in json.dumps(payloads)
        assert any(item.headers.get("type") == "span" for envelope in envelopes for item in envelope.items)
        assert any(item.headers.get("type") == "log" for envelope in envelopes for item in envelope.items)
        assert any(p.get("contexts", {}).get("trace", {}).get("trace_id") == trace_id for p in payloads)
        assert request_id in json.dumps(payloads)
    finally:
        sentry_sdk.get_client().close()
        engine.dispose()
