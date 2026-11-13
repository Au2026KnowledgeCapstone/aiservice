import asyncio

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
from engines.mvp.diagnostics import explain_error
from engines.mvp.exploration import explore, planning_map
from engines.mvp.execution import Execution
from engines.mvp.goal_executor import execute_goal
from engines.mvp.outcome import to_requirement_outcome
from engines.mvp.planner import generate_plan
from engines.mvp.requirement import as_dict


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
        requirement_dict = as_dict(requirement)
        try:
            execution = await self._run(session, requirement_dict, sink)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # An engine returns a value; it does not let an environment flake
            # abort every other requirement in this run.
            execution = Execution(
                outcome="INCONCLUSIVE", classification="environment_failure",
                observed_behavior=explain_error(exc, "preparing the run"),
            )
        return to_requirement_outcome(requirement, execution)

    async def _run(self, session: MvpSession, requirement_dict: dict, sink: EvidenceSink) -> Execution:
        application_map = await self._discover(session, requirement_dict, sink)
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            observation = await session.observe(page)
            if application_map is not None:
                observation["application_map"] = application_map
            plan = await generate_plan(self.provider, requirement_dict, observation, session.target_url)
            return await execute_goal(session, page, plan, requirement_dict, self.provider, sink)
        finally:
            await context.close()

    async def _discover(self, session: MvpSession, requirement_dict: dict, sink: EvidenceSink):
        """Bounded, read-only discovery in its own context — never the context the real run executes in."""
        context, page = await session.open_context()
        try:
            await session.open_landing(page)
            record, _ = await explore(session, page, requirement_dict, sink)
            return planning_map(record)
        finally:
            await context.close()

    async def close(self) -> None:
        return None
