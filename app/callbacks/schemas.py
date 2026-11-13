"""A1 wire contracts derived from BlindSpotBE/backend/main.py callback sketches.

Nested token/amount/digest semantics are provisional until BE implements validation.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, Field


class ExecutionEvent(BaseModel):
    event_id: str
    job_id: str
    attempt: int = 1
    sequence: int
    type: str
    occurred_at: datetime
    schema_version: int = 1
    payload: dict


class UsageEvent(BaseModel):
    usage_id: str
    event_id: str
    revision: int
    cost_session_id: str
    job_id: str
    attempt: int = 1
    provider: str
    model: str
    operation: str
    tokens: dict[str, int | None]
    amount: Decimal | None = None
    currency: str = "USD"
    status: Literal["reserved", "succeeded", "failed", "unknown"]
    timestamps: dict[str, datetime]


class CostFinalization(BaseModel):
    event_id: str
    cost_session_id: str
    revision: int = 1
    final_usage_count: int
    final_usage_digest: str
    closed_at: datetime
