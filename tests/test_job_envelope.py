from copy import deepcopy

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


@pytest.mark.parametrize("field,value", [
    ("job_id", ""), ("run_id", "  "), ("job_id", "x" * 65),
    ("run_id", "x" * 65), ("callback_token", "\t"),
    ("target_url", "not-a-url"), ("target_url", "file:///tmp/page"),
    ("callback_base_url", "ftp://example.test"),
    ("callback_base_url", "https://"), ("engine", "unknown"),
    ("viewport_width", 0), ("viewport_height", -1),
    ("viewport_width", True), ("viewport_height", 1.5),
    ("viewport_width", "1920"), ("requirements", []),
])
def test_invalid_envelope_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate({**REALISTIC_PAYLOAD, field: value})


@pytest.mark.parametrize("path,value", [
    (("id",), " "), (("title",), ""), (("criteria",), []),
    (("criteria", 0, "id"), ""), (("criteria", 0, "text"), "\n"),
])
def test_invalid_requirement_fields_are_rejected(path, value):
    payload = deepcopy(REALISTIC_PAYLOAD)
    target = payload["requirements"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate(payload)


@pytest.mark.parametrize("engine", [None, "stub", "mvp", "graph"])
def test_known_engine_names_are_accepted(engine):
    assert JobEnvelope.model_validate({**REALISTIC_PAYLOAD, "engine": engine}).engine == engine


def test_url_strings_are_preserved_for_storage():
    envelope = JobEnvelope.model_validate(REALISTIC_PAYLOAD)
    stored = envelope.model_dump(mode="json")
    assert stored["target_url"] == REALISTIC_PAYLOAD["target_url"]
    assert stored["callback_base_url"] == REALISTIC_PAYLOAD["callback_base_url"]


@pytest.mark.parametrize("field,value", [
    ("target_url", "https://user:secret@example.test"),
    ("callback_base_url", "https://be.test/internal/v1?token=secret"),
    ("callback_base_url", "https://be.test/internal/v1#fragment"),
    ("callback_base_url", " https://be.test"),
    ("target_url", "https://example.test/\npath"),
])
def test_unsafe_or_ambiguous_urls_are_rejected(field, value):
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate({**REALISTIC_PAYLOAD, field: value})
