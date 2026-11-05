from functools import lru_cache
import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    engine: Literal["mvp", "graph", "stub"] = "mvp"


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
