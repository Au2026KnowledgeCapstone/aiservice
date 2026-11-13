from typing import Callable

from engines.base import AIProvider, Engine

_UNIMPLEMENTED = {
    "graph": "The graph engine is reserved until A5 — see the A5 handoff.",
}


def _stub_factory(*, provider: AIProvider) -> Engine:
    from engines.stub.engine import StubEngine

    return StubEngine()


def _mvp_factory(*, provider: AIProvider) -> Engine:
    from engines.mvp.engine import MvpEngine

    return MvpEngine(provider=provider)


def _unimplemented_factory(name: str) -> Callable[..., Engine]:
    def factory(*, provider: AIProvider) -> Engine:
        raise NotImplementedError(_UNIMPLEMENTED[name])

    return factory


_FACTORIES: dict[str, Callable[..., Engine]] = {
    "stub": _stub_factory,
    "mvp": _mvp_factory,
    "graph": _unimplemented_factory("graph"),
}


def build_engine(name: str, *, provider: AIProvider) -> Engine:
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise ValueError(
            f"Unknown engine {name!r}; valid names are {sorted(_FACTORIES)}"
        ) from None
    return factory(provider=provider)
