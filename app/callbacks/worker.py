"""At-least-once delivery: receivers must deduplicate stable event_id values."""
import asyncio
import logging
from datetime import timedelta
from urllib.parse import quote, urlsplit
import uuid

import httpx
from sqlalchemy import exists, or_, and_, select, update
from sqlalchemy.orm import aliased

from app.callbacks.secrets import token_cipher
from app.callbacks.store import CALLBACK_ROUTES
from app.jobs.models import Job, OutboxEntry, now


logger = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(self, config, sessions, client=None):
        self.config = config
        self.sessions = sessions
        self.client = client
        self.stopped = asyncio.Event()
        self.task = None
        self.cipher = token_cipher(config) if config.service_token.strip() or config.callback_encryption_key else None

    def claim(self):
        timestamp = now()
        previous = aliased(OutboxEntry)
        eligible = (
            OutboxEntry.sequence.is_not(None),
            OutboxEntry.delivered_at.is_(None),
            OutboxEntry.failed_at.is_(None),
            or_(OutboxEntry.next_attempt_at.is_(None), OutboxEntry.next_attempt_at <= timestamp),
            or_(OutboxEntry.lease_until.is_(None), OutboxEntry.lease_until <= timestamp),
        )
        with self.sessions.begin() as db:
            candidate = db.scalar(select(OutboxEntry.id).where(
                *eligible,
                ~exists(select(previous.id).where(
                    previous.job_id == OutboxEntry.job_id,
                    or_(previous.attempt < OutboxEntry.attempt,
                        and_(previous.attempt == OutboxEntry.attempt, previous.sequence < OutboxEntry.sequence)),
                    previous.delivered_at.is_(None),
                )),
            ).order_by(OutboxEntry.job_id, OutboxEntry.attempt, OutboxEntry.sequence).with_for_update(skip_locked=True).limit(1))
            if candidate is None:
                return None
            owner = str(uuid.uuid4())
            result = db.execute(update(OutboxEntry).where(
                OutboxEntry.id == candidate, *eligible,
            ).values(
                lease_owner=owner,
                lease_until=timestamp + timedelta(seconds=max(
                    self.config.callback_lease_seconds, self.config.callback_timeout_seconds + 5,
                )),
                attempts=OutboxEntry.attempts + 1,
            ))
            if result.rowcount != 1:
                return None
            entry = db.get(OutboxEntry, candidate)
            job = db.get(Job, entry.job_id)
            return {
                "id": entry.id, "owner": owner, "attempts": entry.attempts,
                "payload": entry.payload, "kind": entry.kind,
                "base_url": job.envelope["callback_base_url"], "run_id": job.run_id,
                "secret": job.callback_secret,
            }

    def acknowledge(self, entry, error=None, retry=False, retry_after=0):
        timestamp = now()
        values = {"lease_until": None, "lease_owner": None, "last_error": error}
        if error is None:
            values["delivered_at"] = timestamp
        elif retry:
            delay = min(
                self.config.callback_backoff_max_seconds,
                self.config.callback_backoff_seconds * 2 ** min(entry["attempts"] - 1, 20),
            )
            values["next_attempt_at"] = timestamp + timedelta(seconds=max(delay, retry_after))
        else:
            values["failed_at"] = timestamp
        with self.sessions.begin() as db:
            result = db.execute(update(OutboxEntry).where(
                OutboxEntry.id == entry["id"], OutboxEntry.lease_owner == entry["owner"],
                OutboxEntry.delivered_at.is_(None),
            ).values(**values))
        if result.rowcount and error is not None and not retry:
            # IDs and categories only: never log the envelope, URL, token or body.
            logger.error("Permanent callback delivery failure: event_id=%s category=%s", entry["id"], error)

    async def deliver_once(self):
        entry = await asyncio.to_thread(self.claim)
        if entry is None:
            return False
        if not entry["secret"] or self.cipher is None:
            await asyncio.to_thread(self.acknowledge, entry, "MissingCallbackToken")
            return True
        try:
            token = self.cipher.decrypt(entry["secret"].encode()).decode()
        except Exception:
            await asyncio.to_thread(self.acknowledge, entry, "CallbackTokenDecryptionFailed")
            return True
        run_segment = quote(entry["run_id"], safe="")
        if run_segment in (".", ".."):
            run_segment = run_segment.replace(".", "%2E")
        prefix = entry["base_url"].rstrip("/")
        try:
            prefixed = urlsplit(prefix).path.endswith("/internal/v1")
        except ValueError:
            await asyncio.to_thread(self.acknowledge, entry, "InvalidCallbackURL")
            return True
        if not prefixed:
            prefix += "/internal/v1"
        url = (prefix + "/runs/" + run_segment
               + "/" + CALLBACK_ROUTES[entry["kind"]])
        try:
            response = await self.client.post(
                url, json=entry["payload"], headers={"Authorization": f"Bearer {token}"},
                timeout=self.config.callback_timeout_seconds, follow_redirects=False,
            )
        except httpx.TransportError as exc:
            await asyncio.to_thread(self.acknowledge, entry, type(exc).__name__, True)
            return True
        except httpx.InvalidURL:
            await asyncio.to_thread(self.acknowledge, entry, "InvalidCallbackURL")
            return True
        code = response.status_code
        if 200 <= code < 300:
            await asyncio.to_thread(self.acknowledge, entry)
        else:
            retry_after = 0
            try:
                retry_after = max(0, min(float(response.headers.get("Retry-After", "0")), 86400))
            except ValueError:
                pass
            await asyncio.to_thread(
                self.acknowledge, entry, f"HTTP {code}",
                code in (408, 429) or code >= 500, retry_after,
            )
        return True

    async def run(self):
        from sqlalchemy.exc import SQLAlchemyError
        while not self.stopped.is_set():
            try:
                delivered = await self.deliver_once()
            except (SQLAlchemyError, httpx.HTTPError):
                # Leases permit recovery; never log response bodies or credentials.
                delivered = False
            if not delivered:
                try:
                    await asyncio.wait_for(self.stopped.wait(), self.config.callback_poll_seconds)
                except TimeoutError:
                    pass

    async def start(self):
        if self.client is None:
            self.client = httpx.AsyncClient()
            self._owns_client = True
        else:
            self._owns_client = False
        self.task = asyncio.create_task(self.run(), name="callback-delivery")

    async def shutdown(self):
        self.stopped.set()
        if self.task:
            await self.task
        if getattr(self, "_owns_client", False):
            await self.client.aclose()
