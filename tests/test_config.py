import pytest
from pydantic import ValidationError

from app.core.config import settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    settings.cache_clear()
    yield
    settings.cache_clear()


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
