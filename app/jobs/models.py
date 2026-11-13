import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    """Bookkeeping only. `envelope` never carries credentials — see app/jobs/store.py."""

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="received")
    envelope: Mapped[dict] = mapped_column(JSON)
    engine: Mapped[str | None] = mapped_column(String(20))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    callback_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    callback_secret: Mapped[str | None] = mapped_column(Text)


class OutboxEntry(Base):
    """Written before delivery is attempted. That ordering is the durability guarantee."""

    __tablename__ = "outbox"
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_outbox_job_sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.job_id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    sequence: Mapped[int | None] = mapped_column(Integer)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
