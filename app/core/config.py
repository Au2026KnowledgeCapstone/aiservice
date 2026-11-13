from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine: Literal["mvp", "graph", "stub"] = "mvp"
    database_url: str  # aiservice's own operational database. Required.
    service_token: str = Field(default="", repr=False)  # BE presents this on POST /v1/jobs.
    max_concurrent_jobs: int = Field(default=1, ge=1)
    job_timeout_seconds: int = Field(default=1800, ge=1)
    callback_encryption_key: str = Field(default="", repr=False)
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


def _settings_from_sources(*sources: dict) -> Settings:
    values = {}
    for name in Settings.model_fields:
        key = name.upper()
        for scope in sources:
            if key in scope and scope[key] is not None:
                values[name] = scope[key]
                break
    return Settings(**values)


@lru_cache
def settings() -> Settings:
    root_env = Path(__file__).resolve().parents[2] / ".env"
    return _settings_from_sources(os.environ, dotenv_values(".env"), dotenv_values(root_env))
