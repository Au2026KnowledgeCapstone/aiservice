import sys

import pytest

from engines.registry import build_engine


class _FakeProvider:
    async def structured(self, **kwargs):
        raise NotImplementedError


def test_build_stub_satisfies_engine_shape():
    engine = build_engine("stub", provider=_FakeProvider())
    assert engine.name == "stub"
    for attr in ("prepare", "survey", "execute", "close"):
        assert callable(getattr(engine, attr))


def test_build_unknown_raises_with_valid_names():
    with pytest.raises(ValueError) as excinfo:
        build_engine("nonsense", provider=_FakeProvider())
    message = str(excinfo.value)
    assert "stub" in message and "mvp" in message and "graph" in message


def test_build_mvp_and_graph_fail_without_import_error():
    for name in ("mvp", "graph"):
        with pytest.raises(NotImplementedError):
            build_engine(name, provider=_FakeProvider())


def test_selecting_stub_does_not_import_playwright():
    sys.modules.pop("playwright", None)
    build_engine("stub", provider=_FakeProvider())
    assert "playwright" not in sys.modules
