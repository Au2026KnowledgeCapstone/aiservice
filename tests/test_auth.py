import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.db import Base
from app.main import create_app

PAYLOAD = {
    "job_id": "job-1",
    "run_id": "run-1",
    "target_url": "https://example.test",
    "requirements": [{
        "id": "req-1", "title": "Checkout", "description": "",
        "criteria": [{"id": "c1", "text": "Checkout succeeds"}],
    }],
    "callback_base_url": "https://backend.test",
    "callback_token": "callback-token",
}


@pytest.fixture
def client(tmp_path):
    config = Settings(database_url="sqlite://", service_token="service-secret", engine="stub")
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = create_app(config, sessionmaker(bind=engine), start_delivery=False)
    with TestClient(app) as http:
        yield http, config
    engine.dispose()


@pytest.mark.parametrize(
    "authorization", [None, "Basic service-secret", "Bearer", "Bearer wrong-token"]
)
def test_unauthorized_requests_are_rejected(client, authorization):
    http, _ = client
    headers = {} if authorization is None else {"Authorization": authorization}
    response = http.post("/v1/jobs", json=PAYLOAD, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "service-secret" not in response.text
    assert "wrong-token" not in response.text


def test_valid_token_reaches_job_endpoint(client):
    http, _ = client
    response = http.post(
        "/v1/jobs", json=PAYLOAD,
        headers={"Authorization": "Bearer service-secret"},
    )
    assert response.status_code == 202
    assert response.json()["status"] in {"received", "running", "finished"}
    assert {key: response.json()[key] for key in ("job_id", "run_id", "duplicate")} == {"job_id": "job-1", "run_id": "run-1", "duplicate": False}


@pytest.mark.parametrize("token", ["", "   "])
def test_unconfigured_service_cannot_accept_jobs(client, token):
    http, config = client
    config.service_token = token
    response = http.post(
        "/v1/jobs", json=PAYLOAD,
        headers={"Authorization": "Bearer service-secret"},
    )
    assert response.status_code == 503


def test_health_remains_public_without_configured_token(client):
    http, config = client
    config.service_token = ""
    for path in ("/health", "/heath"):
        response = http.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "engine": "stub"}


def test_authenticated_invalid_envelope_returns_422(client):
    http, _ = client
    response = http.post(
        "/v1/jobs", json={**PAYLOAD, "viewport_width": 0},
        headers={"Authorization": "Bearer service-secret"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "viewport_width"]


def test_validation_errors_do_not_echo_credentials(client):
    http, _ = client
    payload = {**PAYLOAD, "credentials": {"password": "private-password"}}
    del payload["run_id"]
    response = http.post("/v1/jobs", json=payload, headers={"Authorization": "Bearer service-secret"})
    assert response.status_code == 422
    assert "private-password" not in response.text
    assert "callback-token" not in response.text
    assert "run_id" in response.text
