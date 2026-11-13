from app.jobs.models import Job
from app.schemas.jobs import JobEnvelope
from sqlalchemy.exc import IntegrityError


class JobConflict(Exception):
    pass


def accept_job(sessions, envelope: JobEnvelope, cipher=None) -> bool:
    """Commit before scheduling. The primary key arbitrates concurrent requests."""
    with sessions() as db:
        try:
            db.add(new_job(envelope, cipher))
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            existing = db.get(Job, envelope.job_id)
            if existing is None:
                raise
            if existing.envelope != envelope_for_storage(envelope):
                raise JobConflict from None
            return False


def envelope_for_storage(envelope: JobEnvelope) -> dict:
    """What gets persisted on a `jobs` row. Credentials never land here."""
    payload = envelope.model_dump(mode="json")
    payload["credentials"] = {}
    payload["callback_token"] = ""
    return payload


def new_job(envelope: JobEnvelope, cipher=None) -> Job:
    return Job(
        job_id=envelope.job_id,
        run_id=envelope.run_id,
        status="received",
        envelope=envelope_for_storage(envelope),
        engine=envelope.engine,
        callback_secret=cipher.encrypt(envelope.callback_token.encode()).decode() if cipher else None,
    )


def job_status(sessions, job_id):
    with sessions() as db:
        return db.get(Job, job_id).status
