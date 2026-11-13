"""A1 callback wire contract, kept in sync with BlindSpotBE schemas/internal.py."""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Annotated
from pydantic import BaseModel, Field, ConfigDict

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExecutionEvent(Wire):
    event_id: Identifier
    job_id: Identifier
    attempt: int = Field(default=1, ge=1)
    sequence: int = Field(gt=0)
    type: Literal["job.running", "job.finished", "job.failed", "job.cancelled", "requirement.completed"]
    occurred_at: datetime
    schema_version: Literal[1] = 1
    payload: dict


class Tokens(Wire):
    reserved_input: int | None = Field(default=None, ge=0)
    input: int | None = Field(default=None, ge=0)
    output: int | None = Field(default=None, ge=0)


class UsageEvent(Wire):
    usage_id: Identifier
    event_id: Identifier
    revision: int = Field(ge=1, le=2)
    cost_session_id: Identifier
    job_id: Identifier
    attempt: int = Field(default=1, ge=1)
    provider: Identifier
    model: Identifier
    operation: Identifier
    tokens: Tokens
    amount: Decimal | None = Field(default=None, ge=0, max_digits=24, decimal_places=12)
    currency: Literal["USD"] = "USD"
    status: Literal["reserved", "succeeded", "failed", "unknown"]
    timestamps: dict[str, datetime]


class CostFinalization(Wire):
    event_id: Identifier
    cost_session_id: Identifier
    revision: Literal[1] = 1
    final_usage_count: int = Field(ge=0)
    final_usage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    closed_at: datetime
