"""Adapt the reference's `structured(instruction, data, PydanticModel) -> ParsedModel`
call shape onto aiservice's injected `AIProvider.structured(*, operation, system, user,
schema: dict, images: bytes) -> dict`.

No multi-turn schema-repair retry here (the reference's `validation_retries`):
aiservice's `AIProvider` protocol has no assistant-message slot to repair into,
and a silent extra call on validation failure would be exactly the
"retry-until-something-works" behavior A4 exists to avoid. A schema mismatch
raises; the caller turns that into `inconclusive`, never a second attempt.
"""
import json

from pydantic import BaseModel, ValidationError

from engines.mvp.errors import EngineError


async def structured(provider, instruction: str, data: dict, schema_type: type[BaseModel], *, images=()) -> BaseModel:
    schema = schema_type.model_json_schema()
    user = json.dumps(data, default=str, ensure_ascii=False)
    result = await provider.structured(
        operation=schema_type.__name__,
        system=instruction,
        user=user,
        schema=schema,
        images=tuple(images),
    )
    try:
        return schema_type.model_validate(result)
    except ValidationError as exc:
        raise EngineError(f"AI response did not match the required schema for {schema_type.__name__}.") from exc
