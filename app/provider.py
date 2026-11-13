"""A1 metering boundary. No real model SDK, automatic model retry, or pricing guesses."""
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.callbacks.schemas import CostFinalization, UsageEvent
from app.callbacks.store import enqueue
from app.core.db import Base
from app.jobs.models import Job, now


class UsageRecord(Base):
    __tablename__ = "ai_usage"
    usage_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id", ondelete="CASCADE"), index=True)
    cost_session_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    operation: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    tokens: Mapped[dict] = mapped_column(JSON)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


def cost_session_id(job_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"blindspot:cost-session:{job_id}:1"))


def usage_event_id(usage_id, revision):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"blindspot:usage:{usage_id}:{revision}"))


def emit_usage(db, record):
    event = UsageEvent(
        usage_id=record.usage_id, event_id=usage_event_id(record.usage_id, record.revision),
        revision=record.revision, cost_session_id=record.cost_session_id, job_id=record.job_id,
        provider=record.provider, model=record.model, operation=record.operation,
        tokens=record.tokens, amount=record.amount, currency=record.currency,
        status=record.status,
        timestamps={"reserved_at": record.created_at, **(
            {"finalized_at": record.finished_at} if record.finished_at else {}
        )},
    )
    enqueue(db, record.job_id, "ai_usage", event.model_dump(mode="json"), event_id=event.event_id)


def finalize_costs(db, job_id):
    """Close once, after all final usage messages have been persisted in this transaction."""
    job = db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
    records = db.scalars(select(UsageRecord).where(UsageRecord.job_id == job_id).order_by(UsageRecord.usage_id)).all()
    for record in records:
        if record.status == "reserved":
            # The process cannot know whether a provider charged an interrupted request.
            record.status = "unknown"
            record.revision += 1
            record.finished_at = now()
            emit_usage(db, record)
    digest_items = [{
        "usage_id": r.usage_id, "revision": r.revision, "status": r.status,
        "tokens": r.tokens, "amount": str(r.amount) if r.amount is not None else None,
        "currency": r.currency,
    } for r in records]
    digest = hashlib.sha256(json.dumps(digest_items, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    session_id = cost_session_id(job_id)
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"blindspot:cost-finalization:{session_id}:1"))
    event = CostFinalization(
        event_id=event_id, cost_session_id=session_id, final_usage_count=len(records),
        final_usage_digest=digest, closed_at=now(),
    )
    enqueue(db, job_id, "cost_finalization", event.model_dump(mode="json"), event_id=event_id)
    job.costs_closed = True


@dataclass(frozen=True)
class ProviderResponse:
    result: dict
    input_tokens: int | None = None
    output_tokens: int | None = None
    amount: Decimal | None = None
    currency: str = "USD"


class NullProvider:
    name = "null"
    model = "none"

    async def request(self, **kwargs) -> ProviderResponse:
        raise RuntimeError("No AI transport is configured in A1")

    async def structured(self, **kwargs) -> dict:
        raise RuntimeError("No AI transport is configured in A1")


class MeteredProvider:
    def __init__(self, sessions, job_id, transport=None):
        self.sessions = sessions
        self.job_id = job_id
        self.transport = transport or NullProvider()
        self.lock = asyncio.Lock()

    def reserve(self, usage_id, operation, reserved_input):
        with self.sessions.begin() as db:
            job = db.scalar(select(Job).where(Job.job_id == self.job_id).with_for_update())
            if job.costs_closed:
                raise RuntimeError("Cost session is already closed")
            record = UsageRecord(
                usage_id=usage_id, job_id=self.job_id, cost_session_id=cost_session_id(self.job_id),
                revision=1, provider=self.transport.name, model=self.transport.model,
                operation=operation, status="reserved", created_at=now(),
                tokens={"reserved_input": reserved_input, "input": None, "output": None},
                amount=None, currency="USD",
            )
            db.add(record)
            emit_usage(db, record)

    def finish(self, usage_id, status, response=None):
        with self.sessions.begin() as db:
            # Same lock order as reservation and cost finalization.
            db.scalar(select(Job).where(Job.job_id == self.job_id).with_for_update())
            record = db.get(UsageRecord, usage_id)
            if record.status != "reserved":
                return
            record.status = status
            record.revision += 1
            record.finished_at = now()
            if response is not None:
                for count in (response.input_tokens, response.output_tokens):
                    if count is not None and (type(count) is not int or count < 0):
                        raise ValueError("Invalid token count")
                if response.amount is not None and (
                    not response.amount.is_finite() or response.amount < 0
                ):
                    raise ValueError("Invalid request cost")
                record.tokens = {**record.tokens, "input": response.input_tokens, "output": response.output_tokens}
                record.amount = response.amount
                record.currency = response.currency
            emit_usage(db, record)

    async def db_call(self, fn, *args):
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def structured(self, *, operation, system, user, schema, images=()):
        async with self.lock:
            usage_id = str(uuid.uuid4())
            # Conservative text-byte reservation; never reported as measured tokens.
            # Image token estimates require a real transport/model and remain unknown.
            reserved_input = len(json.dumps({"system": system, "user": user, "schema": schema}, ensure_ascii=False).encode()) + 1024
            try:
                await self.db_call(self.reserve, usage_id, operation, reserved_input)
            except asyncio.CancelledError:
                await self.db_call(self.finish, usage_id, "unknown")
                raise
            try:
                response = await self.transport.request(
                    operation=operation, system=system, user=user, schema=schema, images=images,
                )
                if not isinstance(response, ProviderResponse) or not isinstance(response.result, dict):
                    raise ValueError("Invalid provider response")
                await self.db_call(self.finish, usage_id, "succeeded", response)
                return response.result
            except asyncio.CancelledError:
                await self.db_call(self.finish, usage_id, "unknown")
                raise
            except Exception:
                # Failed calls may still be billable. Do not guess a zero cost.
                await self.db_call(self.finish, usage_id, "failed")
                raise
