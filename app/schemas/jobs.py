from pydantic import BaseModel

from engines.base import CriterionSnapshot, RequirementSnapshot


class CriterionSpec(BaseModel):
    id: str
    text: str


class RequirementSpec(BaseModel):
    id: str
    title: str
    description: str
    criteria: list[CriterionSpec]


class JobEnvelope(BaseModel):
    job_id: str  # idempotency key. The same job twice runs once.
    run_id: str  # BE's analysis_runs.id — used on every callback
    engine: str | None = None  # None means "whatever ENGINE says"
    target_url: str
    viewport_width: int = 1920
    viewport_height: int = 1080
    credentials: dict[str, str] = {}
    requirements: list[RequirementSpec]
    callback_base_url: str  # where to POST results
    callback_token: str  # job-scoped. Not a shared forever-key.


def to_requirement_snapshot(spec: RequirementSpec) -> RequirementSnapshot:
    return RequirementSnapshot(
        id=spec.id,
        title=spec.title,
        description=spec.description,
        criteria=tuple(
            CriterionSnapshot(id=criterion.id, text=criterion.text)
            for criterion in spec.criteria
        ),
    )
