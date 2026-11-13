import subprocess
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


def test_build_graph_fails_without_import_error():
    with pytest.raises(NotImplementedError):
        build_engine("graph", provider=_FakeProvider())


def test_build_mvp_satisfies_engine_shape():
    engine = build_engine("mvp", provider=_FakeProvider())
    assert engine.name == "mvp"
    for attr in ("prepare", "survey", "execute", "close"):
        assert callable(getattr(engine, attr))


def test_selecting_stub_does_not_import_playwright():
    # Run in a subprocess: sys.modules is process-global, so checking it in this
    # process would pass or fail depending on what another test imported first.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from engines.registry import build_engine; "
            "build_engine('stub', provider=None); "
            "sys.exit(1 if 'playwright' in sys.modules else 0)",
        ],
        check=True,
    )
