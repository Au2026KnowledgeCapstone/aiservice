"""Adapt engines.base.RequirementSnapshot to the dict shape the ported reference code expects.

The reference reads `requirement['acceptance_criteria']` (a list of strings,
addressed by position) and `requirement.get('id'|'title'|'description'|'source')`
from a DB-backed dict. `RequirementSnapshot.criteria` is a sequence of
`CriterionSnapshot(id, text)` instead — criterion identity is a string `id`,
not a position. `as_dict` bridges the two; `criterion_id_at` recovers the
original `CriterionSnapshot.id` for a given `criterion_index` so outcomes can
cite the real id rather than a position.
"""
from engines.base import RequirementSnapshot


def as_dict(requirement: RequirementSnapshot) -> dict:
    return {
        "id": requirement.id,
        "title": requirement.title,
        "description": requirement.description,
        "acceptance_criteria": [criterion.text for criterion in requirement.criteria],
        "source": "",
    }


def criterion_id_at(requirement: RequirementSnapshot, criterion_index: int) -> str:
    return requirement.criteria[criterion_index].id
