from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.jobs.models import Job
from app.jobs.store import new_job
from app.schemas.jobs import JobEnvelope

ENVELOPE = JobEnvelope.model_validate(
    {
        "job_id": "job-1",
        "run_id": "run-1",
        "target_url": "https://example.test",
        "credentials": {"username": "demo", "password": "hunter2"},
        "requirements": [
            {
                "id": "req-1",
                "title": "Checkout works",
                "description": "A user can complete checkout.",
                "criteria": [{"id": "c1", "text": "Cart total matches line items."}],
            }
        ],
        "callback_base_url": "https://blindspotbe.internal/internal/v1",
        "callback_token": "job-scoped-token",
    }
)


def test_persisted_envelope_contains_no_credential_values():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(new_job(ENVELOPE))
        session.commit()

    with Session(engine) as session:
        row = session.scalar(select(Job).where(Job.job_id == "job-1"))

    assert row is not None
    assert row.envelope["credentials"] == {}
    serialized = str(row.envelope)
    assert "hunter2" not in serialized
    assert "demo" not in serialized
