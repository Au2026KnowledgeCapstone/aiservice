"""Service-authenticated retrieval of job-bound operational evidence."""
import base64
import hashlib
from sqlalchemy import select
from app.jobs.models import OutboxEntry
from app.capabilities.errors import CapabilityError
from app.capabilities.schemas import EvidenceResponse


def retrieve(sessions, job_id, evidence_id):
    with sessions() as db:
        row = db.scalar(select(OutboxEntry).where(OutboxEntry.id == evidence_id,
            OutboxEntry.job_id == job_id, OutboxEntry.kind == 'evidence', OutboxEntry.sequence.is_(None)))
        if row is None:
            raise CapabilityError("Evidence not found for this job.", 404)
        payload = row.payload
    try:
        if payload.get('kind') == 'screenshot':
            data = base64.b64decode(payload['base64'], validate=True)
            kind, media = 'screenshot', 'image/png'
        else:
            data = payload['text'].encode()
            kind, media = ('log' if payload.get('kind') == 'log' else 'page_text'), 'text/plain; charset=utf-8'
    except (KeyError, ValueError, AttributeError):
        raise CapabilityError("Evidence data is invalid.", 502) from None
    if len(data) > 10 * 1024 * 1024:
        raise CapabilityError("Evidence exceeds the retrieval limit.", 413)
    return EvidenceResponse(id=evidence_id, job_id=job_id, type=kind, media_type=media,
        content_base64=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest(),
        captured_at=payload.get('captured_at'))
