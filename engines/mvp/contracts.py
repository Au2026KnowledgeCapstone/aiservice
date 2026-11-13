"""Semantic locators and the engine's configuration stand-in.

`Locator` mirrors BlindSpot/backend/app/schemas/__init__.py's `Locator` shape
(exactly one semantic strategy; a role locator carries its accessible name).
`ObjectiveCheck` mirrors the reference's shape too, but nothing in aiservice's
job envelope constructs one yet — see the A4 handoff notes: there is no
BrowserConfiguration-equivalent channel, so `EngineConfiguration.objective_checks`
is always empty for now. If BE's contract grows a field for these, this is
where the real (validated) construction path belongs.
"""
from dataclasses import dataclass, field
from typing import Literal, Sequence


@dataclass(frozen=True)
class Locator:
    role: str | None = None
    name: str | None = None
    label: str | None = None
    placeholder: str | None = None
    text: str | None = None
    test_id: str | None = None


@dataclass(frozen=True)
class ObjectiveCheck:
    requirement_id: str
    criterion_index: int
    criterion_text: str
    kind: Literal["url_path", "text_visible", "text_absent", "enabled", "disabled", "value", "count"]
    target: Locator | None = None
    expected: str = ""
    after_step: int | None = None


@dataclass(frozen=True)
class EngineConfiguration:
    """Stands in for the reference's BrowserConfiguration. Always empty in A4."""

    objective_checks: Sequence[ObjectiveCheck] = field(default_factory=tuple)


def locate(page, target: Locator):
    if target.role:
        return page.get_by_role(target.role, name=target.name, exact=True)
    if target.label:
        return page.get_by_label(target.label, exact=True)
    if target.placeholder:
        return page.get_by_placeholder(target.placeholder, exact=True)
    if target.text:
        return page.get_by_text(target.text, exact=True)
    return page.get_by_test_id(target.test_id)
