"""Adapt the reference's `structured(instruction, data, PydanticModel) -> ParsedModel`
call shape onto aiservice's injected `AIProvider.structured(*, operation, system, user,
schema: dict, images: bytes) -> dict`.

No multi-turn schema-repair retry here (the reference's `validation_retries`):
aiservice's `AIProvider` protocol has no assistant-message slot to repair into,
and a silent extra call on validation failure would be exactly the
"retry-until-something-works" behavior A4 exists to avoid. A schema mismatch
raises; the caller turns that into `inconclusive`, never a second attempt.

`wire_schema` lets a caller send a *narrower* schema than `schema_type` for one
call — specifically, to pin a field to an enum of values only that call site
knows (see `planner.choose_element`, which enumerates the current shortlist's
element IDs so strict structured-output decoding cannot name a control that is
not on the page). It constrains generation only: `schema_type` still parses the
response and still names the operation for usage accounting, and nothing here
retries. Prevention, not repair.
"""
import json

from pydantic import BaseModel, ValidationError

from engines.mvp.errors import EngineError


async def structured(provider, instruction: str, data: dict, schema_type: type[BaseModel], *, images=(), wire_schema: dict | None = None) -> BaseModel:
    schema = schema_type.model_json_schema() if wire_schema is None else wire_schema
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
        # Field path and rule only -- never `input`, which would echo model output
        # or page content into a user-facing message. Without this the whole run
        # reports "did not match the required schema" and nobody can tell which
        # of a fifteen-step plan was wrong, or why.
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'response'}: {error['msg']}"
            for error in exc.errors()[:3]
        )
        raise EngineError(
            f"AI response did not match the required schema for {schema_type.__name__}"
            + (f" ({detail})." if detail else ".")
        ) from exc
