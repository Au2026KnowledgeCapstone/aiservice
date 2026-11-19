from functools import lru_cache
import os
import json
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sentry_dsn: str = Field(default="", repr=False)
    sentry_environment: str = "development"
    sentry_release: str | None = None
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0, le=1)
    # JSON array of anchored regexes. Empty keeps headers off arbitrary targets.
    sentry_trace_propagation_targets: list[str] = Field(default_factory=list)

    engine: Literal["mvp", "graph", "stub"] = "mvp"
    database_url: str  # aiservice's own operational database. Required.
    service_token: str = Field(default="", repr=False)  # BE presents this on POST /v1/jobs.
    max_concurrent_jobs: int = Field(default=1, ge=1)
    job_timeout_seconds: int = Field(default=1800, ge=1)
    callback_encryption_key: str = Field(default="", repr=False)
    evidence_sink: Literal["outbox", "gcs"] = "outbox"
    gcs_evidence_bucket: str = ""
    extraction_api_url: str = "https://api.openai.com/v1"
    extraction_api_key: str = Field(default="", repr=False)
    extraction_model: str = ""
    extraction_timeout_seconds: float = Field(default=80, ge=1, le=120)
    verification_timeout_seconds: float = Field(default=30, ge=1, le=60)
    verification_enabled: bool = False
    verification_allowed_private_hosts: str = ""
    verification_browser_executable: str = ""
    callback_poll_seconds: float = Field(default=1.0, gt=0)
    callback_timeout_seconds: float = Field(default=10.0, gt=0)
    callback_lease_seconds: float = Field(default=30.0, gt=0)
    callback_backoff_seconds: float = Field(default=1.0, gt=0)
    callback_backoff_max_seconds: float = Field(default=300.0, gt=0)
    ai_api_url: str = "https://api.openai.com/v1"
    ai_api_key: str = Field(default="", repr=False)
    ai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = Field(default=90, ge=1, le=180)


def _settings_from_sources(*sources: dict) -> Settings:
    values = {}
    for name in Settings.model_fields:
        key = name.upper()
        for scope in sources:
            if key in scope and scope[key] is not None:
                values[name] = json.loads(scope[key]) if name == "sentry_trace_propagation_targets" and isinstance(scope[key], str) else scope[key]
                break
    return Settings(**values)


@lru_cache
def settings() -> Settings:
    root_env = Path(__file__).resolve().parents[2] / ".env"
    return _settings_from_sources(os.environ, dotenv_values(".env"), dotenv_values(root_env))
