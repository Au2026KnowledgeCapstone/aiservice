from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, HttpUrl, TypeAdapter

from engines.base import CriterionSnapshot, RequirementSnapshot


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Must not be blank")
    return value


_http_url = TypeAdapter(HttpUrl)


def _validate_http_url(value: str) -> str:
    _http_url.validate_python(value)
    return value


NonBlank = Annotated[str, Field(min_length=1), AfterValidator(_not_blank)]
JobId = Annotated[NonBlank, Field(max_length=64)]
HttpUrlString = Annotated[str, AfterValidator(_validate_http_url)]
PositiveDimension = Annotated[int, Field(strict=True, gt=0)]


class CriterionSpec(BaseModel):
    id: NonBlank
    text: NonBlank


class RequirementSpec(BaseModel):
    id: NonBlank
    title: NonBlank
    description: str
    criteria: list[CriterionSpec] = Field(min_length=1)


class JobEnvelope(BaseModel):
    job_id: JobId  # idempotency key. The same job twice runs once.
    run_id: JobId  # BE's analysis_runs.id — used on every callback
    engine: Literal["mvp", "graph", "stub"] | None = None
    target_url: HttpUrlString
    viewport_width: PositiveDimension = 1920
    viewport_height: PositiveDimension = 1080
    credentials: dict[str, str] = Field(default_factory=dict, repr=False)
    requirements: list[RequirementSpec] = Field(min_length=1)
    callback_base_url: HttpUrlString  # where to POST results
    callback_token: NonBlank = Field(repr=False)  # job-scoped token


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
