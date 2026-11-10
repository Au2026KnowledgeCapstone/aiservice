from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture(autouse=True)
def _ignore_local_dotenv(monkeypatch):
    monkeypatch.setattr("app.core.config.dotenv_values", lambda *args, **kwargs: {})


@pytest.fixture(autouse=True)
def _default_database_url(monkeypatch):
    # Tests that care about DATABASE_URL's absence unset it themselves, after
    # this fixture runs, using the same (function-scoped) monkeypatch instance.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://test:test@localhost:5432/test")


def test_default_engine_is_mvp(monkeypatch):
    monkeypatch.delenv("ENGINE", raising=False)
    assert settings().engine == "mvp"


def test_graph_resolves(monkeypatch):
    monkeypatch.setenv("ENGINE", "graph")
    assert settings().engine == "graph"


def test_unknown_engine_raises_at_construction(monkeypatch):
    monkeypatch.setenv("ENGINE", "nonsense")
    with pytest.raises(ValidationError):
        settings()


def test_missing_database_url_raises_at_construction(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        settings()


def test_job_setting_defaults():
    resolved = settings()
    assert resolved.service_token == ""
    assert resolved.max_concurrent_jobs == 1
    assert resolved.job_timeout_seconds == 1800


def test_env_example_names_every_field():
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    for name in Settings.model_fields:
        assert name.upper() in env_example, f"{name.upper()} missing from .env.example"
