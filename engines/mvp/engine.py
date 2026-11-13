from engines.base import (
    AIProvider,
    BaseEngine,
    Credentials,
    EvidenceSink,
    RequirementOutcome,
    RequirementSnapshot,
    Session,
    Target,
)
from engines.mvp.browser import MvpSession, open_session


class MvpEngine(BaseEngine):
    name = "mvp"

    def __init__(self, *, provider: AIProvider):
        self.provider = provider

    async def prepare(self, target: Target, credentials: Credentials) -> Session:
        return await open_session(target, credentials)

    async def execute(
        self,
        session: Session,
        requirement: RequirementSnapshot,
        sink: EvidenceSink,
    ) -> RequirementOutcome:
        assert isinstance(session, MvpSession)
        raise NotImplementedError("engines/mvp execute() lands across A4 Steps 2-5")

    async def close(self) -> None:
        return None
