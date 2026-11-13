from datetime import datetime
from sqlalchemy import String, Text, DateTime, JSON, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base
from app.jobs.models import now
from decimal import Decimal


class CapabilityOperation(Base):
    __tablename__ = "capability_operations"
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CapabilityUsage(Base):
    __tablename__ = "capability_usage"
    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
