"""Durable idempotency for synchronous capabilities; never reissue unknown model calls."""
import asyncio
import hashlib
import json
from sqlalchemy.exc import IntegrityError
from app.capabilities.models import CapabilityOperation, CapabilityUsage
from app.capabilities.errors import CapabilityError
from app.capabilities.documents import normalize
from app.capabilities.extraction import Extractor
from app.capabilities.verification import BrowserVerifier


class Capabilities:
    def __init__(self, config, sessions, extractor=None, verifier=None):
        self.config, self.sessions = config, sessions
        self.extractor = extractor or Extractor(config)
        self.verifier = verifier or BrowserVerifier(config)
        self.semaphore = asyncio.Semaphore(config.max_concurrent_jobs)

    def claim(self, kind, body):
        fingerprint = hashlib.sha256(json.dumps(body.model_dump(mode="json", exclude={"credentials"}), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.sessions() as db:
            try:
                db.add(CapabilityOperation(request_id=body.request_id, kind=kind, fingerprint=fingerprint, status='processing'))
                db.commit()
                return None
            except IntegrityError:
                db.rollback()
                prior = db.get(CapabilityOperation, body.request_id)
                if prior is None:
                    raise
                if prior.fingerprint != fingerprint or prior.kind != kind:
                    raise CapabilityError("Request ID was reused with changed input.", 409)
                if prior.status == 'finished':
                    return prior.result
                # An interrupted provider call may already be billed. Never auto-resend.
                raise CapabilityError("Request is processing or requires operator review after failure.", 503)

    def usage(self, request_id, status, tokens=None):
        with self.sessions.begin() as db:
            row = db.get(CapabilityUsage, request_id)
            if row is None:
                row = CapabilityUsage(request_id=request_id, status='reserved', revision=1)
                db.add(row)
            if status != 'reserved':
                row.status, row.revision = status, 2
                if tokens:
                    row.input_tokens, row.output_tokens = tokens.get('input_tokens'), tokens.get('output_tokens')
            # No provider pricing is configured; amount remains explicitly unknown.

    def finish(self, identifier, result=None, error=None):
        with self.sessions.begin() as db:
            row = db.get(CapabilityOperation, identifier)
            row.status = 'finished' if result is not None else 'failed'
            row.result = result
            row.error = type(error).__name__ if error else None

    async def run(self, kind, body):
        component = self.extractor if kind == 'import' else self.verifier
        component.configured()
        # Parse before reserving an operation/model call so invalid files are retryable.
        parsed = await asyncio.to_thread(normalize, body.documents) if kind == 'import' else None
        cached = await asyncio.to_thread(self.claim, kind, body)
        if cached is not None:
            return cached
        try:
            async with self.semaphore:
                if kind == 'import':
                    await asyncio.to_thread(self.usage, body.request_id, 'reserved')
                    result, tokens = await self.extractor.extract(body, *parsed)
                    await asyncio.to_thread(self.usage, body.request_id, 'succeeded', tokens)
                else:
                    result = await self.verifier.verify(body)
                serialized = result.model_dump(mode='json')
                await asyncio.to_thread(self.finish, body.request_id, serialized)
                return serialized
        except BaseException as error:
            if kind == 'import':
                await asyncio.shield(asyncio.to_thread(self.usage, body.request_id, 'unknown'))
            await asyncio.shield(asyncio.to_thread(self.finish, body.request_id, None, error))
            if isinstance(error, (CapabilityError, asyncio.CancelledError)):
                raise
            raise CapabilityError("Capability execution failed.", 503) from None
