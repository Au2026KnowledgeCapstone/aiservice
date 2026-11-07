from app.jobs.models import Job
from app.schemas.jobs import JobEnvelope


def envelope_for_storage(envelope: JobEnvelope) -> dict:
    """What gets persisted on a `jobs` row. Credentials never land here."""
    payload = envelope.model_dump(mode="json")
    payload["credentials"] = {}
    return payload


def new_job(envelope: JobEnvelope) -> Job:
    return Job(
        job_id=envelope.job_id,
        run_id=envelope.run_id,
        status="received",
        envelope=envelope_for_storage(envelope),
        engine=envelope.engine,
    )
