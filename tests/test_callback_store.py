from app.callbacks.secrets import token_cipher
from app.callbacks.store import execution_event
from app.jobs.store import accept_job
from app.jobs.models import Job
from app.schemas.jobs import JobEnvelope
from tests.test_auth import PAYLOAD
from tests.test_intake import database, config


def test_durable_sequence_and_encrypted_token(database):
    cipher = token_cipher(config())
    envelope = JobEnvelope.model_validate(PAYLOAD)
    assert accept_job(database, envelope, cipher)
    with database.begin() as db:
        first = execution_event(db, envelope.job_id, "job.running", {})
        second = execution_event(db, envelope.job_id, "job.finished", {})
        assert [first.sequence, second.sequence] == [1, 2]
        assert first.payload["event_id"] == first.id
    with database() as db:
        job = db.get(Job, envelope.job_id)
        assert envelope.callback_token not in str(job.envelope)
        assert envelope.callback_token not in job.callback_secret
        assert cipher.decrypt(job.callback_secret.encode()).decode() == envelope.callback_token


def test_execution_sequences_restart_per_attempt_not_delivery_retry(database):
    import pytest
    accept_job(database, JobEnvelope.model_validate(PAYLOAD), token_cipher(config()))
    with database.begin() as db:
        first = execution_event(db, "job-1", "job.running", {}, attempt=1)
        second = execution_event(db, "job-1", "job.failed", {}, attempt=1)
        retry = execution_event(db, "job-1", "job.running", {}, attempt=2)
        assert [(r.attempt, r.sequence) for r in (first, second, retry)] == [(1, 1), (1, 2), (2, 1)]
        assert retry.payload["attempt"] == 2
    with database.begin() as db, pytest.raises(ValueError, match="backwards"):
        execution_event(db, "job-1", "job.running", {}, attempt=1)
