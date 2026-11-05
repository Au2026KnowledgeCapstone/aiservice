from dataclasses import dataclass

from engines.base import (
    BaseEngine,
    Credentials,
    CriterionVerdict,
    EvidenceSink,
    RequirementOutcome,
    RequirementSnapshot,
    Session,
    Target,
)


@dataclass(frozen=True)
class _StubSession:
    async def close(self) -> None:
        return None


class StubEngine(BaseEngine):
    name = "stub"

    async def prepare(self, target: Target, credentials: Credentials) -> Session:
        return _StubSession()

    async def execute(
        self,
        session: Session,
        requirement: RequirementSnapshot,
        sink: EvidenceSink,
    ) -> RequirementOutcome:
        evidence_id = await sink.put_text(
            f"Stub engine received requirement {requirement.id!r}.", kind="stub"
        )
        criterion_verdicts = tuple(
            CriterionVerdict(
                criterion_id=criterion.id,
                verdict="inconclusive",
                rationale="Stub engine never reaches a verdict.",
                cited_evidence_ids=(evidence_id,),
                source="engine",
            )
            for criterion in requirement.criteria
        )
        return RequirementOutcome(
            verdict="inconclusive",
            classification="execution_error",
            observed_behavior="Stub engine does not execute anything.",
            criterion_verdicts=criterion_verdicts,
            actions=(),
        )

    async def close(self) -> None:
        return None
