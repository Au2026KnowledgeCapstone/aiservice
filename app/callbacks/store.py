import uuid
from sqlalchemy import update, case
from app.callbacks.schemas import ExecutionEvent
from app.jobs.models import Job, OutboxEntry, now

CALLBACK_ROUTES = {
    "execution_event": "execution-events",
    "ai_usage": "ai-usage",
    "cost_finalization": "ai-cost-finalizations",
}


def enqueue(db, job_id: str, kind: str, payload: dict, *, event_id=None, attempt=None):
    """Caller owns the transaction. IDs and sequence survive every delivery retry."""
    if kind not in CALLBACK_ROUTES:
        raise ValueError("Unknown callback kind")
    event_id = event_id or str(uuid.uuid4())
    existing = db.get(OutboxEntry, event_id)
    if existing is not None:
        if existing.job_id != job_id or existing.kind != kind:
            raise ValueError("Event ID collision")
        return existing
    attempt = attempt if attempt is not None else payload.get("attempt", 1)
    if type(attempt) is not int or attempt < 1:
        raise ValueError("Attempt must be a positive integer")
    allocation = db.execute(
        update(Job).where(Job.job_id == job_id, Job.callback_attempt <= attempt)
        .values(callback_sequence=case((Job.callback_attempt == attempt, Job.callback_sequence + 1), else_=1),
                callback_attempt=attempt)
        .returning(Job.callback_sequence)
    ).scalar_one_or_none()
    if allocation is None:
        raise ValueError("Job not found or attempt moved backwards")
    sequence = allocation
    if kind == "execution_event":
        payload = ExecutionEvent(
            event_id=event_id, job_id=job_id, attempt=attempt, sequence=sequence,
            type=payload["type"], occurred_at=now(), payload=payload["payload"],
        ).model_dump(mode="json")
    else:
        payload = {**payload, "event_id": event_id}
    entry = OutboxEntry(
        id=event_id, job_id=job_id, kind=kind, attempt=attempt, sequence=sequence, payload=payload,
    )
    db.add(entry)
    db.flush()
    return entry


def execution_event(db, job_id, event_type, payload, *, attempt=1):
    return enqueue(db, job_id, "execution_event", {"type": event_type, "payload": payload}, attempt=attempt)
