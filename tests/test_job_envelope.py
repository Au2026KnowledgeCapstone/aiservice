import pytest
from pydantic import ValidationError

from app.schemas.jobs import JobEnvelope, to_requirement_snapshot

REALISTIC_PAYLOAD = {
    "job_id": "job-1",
    "run_id": "run-1",
    "engine": None,
    "target_url": "https://example.test",
    "credentials": {"username": "demo", "password": "hunter2"},
    "requirements": [
        {
            "id": "req-1",
            "title": "Checkout works",
            "description": "A user can complete checkout.",
            "criteria": [
                {"id": "c1", "text": "Cart total matches line items."},
                {"id": "c2", "text": "Confirmation email is sent."},
            ],
        }
    ],
    "callback_base_url": "https://blindspotbe.internal/internal/v1",
    "callback_token": "job-scoped-token",
}


def test_parses_a_realistic_payload():
    envelope = JobEnvelope.model_validate(REALISTIC_PAYLOAD)
    assert envelope.job_id == "job-1"
    assert envelope.viewport_width == 1920
    assert envelope.viewport_height == 1080
    assert len(envelope.requirements) == 1


def test_mapper_round_trips_requirement_fields():
    envelope = JobEnvelope.model_validate(REALISTIC_PAYLOAD)
    spec = envelope.requirements[0]
    snapshot = to_requirement_snapshot(spec)

    assert snapshot.id == spec.id
    assert snapshot.title == spec.title
    assert snapshot.description == spec.description
    assert len(snapshot.criteria) == len(spec.criteria)
    for snapshot_criterion, spec_criterion in zip(snapshot.criteria, spec.criteria):
        assert snapshot_criterion.id == spec_criterion.id
        assert snapshot_criterion.text == spec_criterion.text


def test_missing_callback_token_is_rejected():
    payload = {key: value for key, value in REALISTIC_PAYLOAD.items() if key != "callback_token"}
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate(payload)
