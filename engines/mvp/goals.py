"""AI-facing contracts contain element references, never executable locators.

Ported from BlindSpot/backend/app/services/browser/goals.py. `Strict`/`Priority`/
`Testability` are recreated locally (engines/ cannot import app.schemas).
Sequenced into Step 3 rather than Step 5 because action_contracts.py's
`compile_action` operates on `GoalStep` directly; planner.py/assessment.py
(the AI-calling code that produces/consumes these types) still land in Step 5.
"""
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

Priority = Literal["low", "medium", "high", "critical"]
Testability = Literal["automatable", "manual", "requires_clarification"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalContract(Strict):
    strict_output: ClassVar[bool] = True


class GoalStep(GoalContract):
    action: Literal["navigate", "click", "fill", "select", "check", "press", "wait", "read", "observe", "screenshot"]
    purpose: Literal[
        "auto", "navigate", "activate", "focus", "enter_text", "select_option",
        "set_checked", "dismiss", "submit", "keyboard", "inspect",
    ] = Field(
        default="auto",
        description="Semantic goal; auto is reserved for legacy plans. The backend derives a compatible operation "
        "from the freshly observed control. Use activate for selecting records/logs or updating a detail panel, "
        "navigate for Back/Home or opening another page, dismiss only for closing a popup, and keyboard only for "
        "an explicit keyboard requirement.",
    )
    destination_label: str | None = Field(default=None, max_length=200, description="Semantic destination such as Dashboard, without inventing a URL or destination region.")
    checked: bool | None = Field(default=None, description="Desired toggle state for set_checked; never blindly toggle a checkbox.")
    intent: str = Field(min_length=1, max_length=1000)
    target_description: str | None = Field(default=None, max_length=500, description="Describe the intended control. Backend selects it from a fresh observed element inventory, not generated locators.")
    expected_change: Literal["auto", "content", "url", "none"] = Field(default="auto", description="Wait for changed page data, a changed URL, or no page transition. Use content when switching records or loading different details.")
    change_region: str | None = Field(default=None, max_length=200, description="Initial-page region for a same-page update only. Leave null for navigation and all later page states; the executor binds checks from a fresh observation. Never guess destination regions.")
    value: str | None = Field(default=None, max_length=1000)
    url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def valid_action(self):
        # Parse-time rejection costs the whole plan and the whole run, so this
        # validator only refuses what no page could make valid. Anything the
        # engine grounds against an observation belongs to compile_action, which
        # fails one step with a precise reason and lets the rest proceed. A
        # navigate step with no url is the example: known_destination() can still
        # ground one from the chosen control, the requirement text or an observed
        # destination, and refuses with "Navigation needs an observed control or
        # a grounded destination URL" when it genuinely cannot.
        #
        # A submit step's action and value are both replaced during compilation
        # (click for a submit button, Enter for a text field), so a planned press
        # that carries no key is not a contract violation there -- demanding one
        # here is stricter than the engine itself and rejects the whole plan over
        # a field the compiler was always going to overwrite.
        supplied_on_compile = self.action == "press" and self.purpose == "submit"
        if self.action in {"fill", "select", "press"} and self.value is None and not supplied_on_compile:
            raise ValueError("Action requires value")
        if self.action == "press" and self.value is not None and self.value not in {"Enter", "Tab", "Escape", "Space", "ArrowDown", "ArrowUp"}:
            raise ValueError("Unsupported key")
        return self


class GoalPlan(GoalContract):
    objective: str = Field(min_length=1, max_length=2000)
    preconditions: list[str] = Field(max_length=20)
    expected_behavior: str = Field(min_length=1, max_length=3000)
    # Nothing in the engine reads this -- it travels to the assessor as plan
    # context only. Refusing an empty list cost the whole run for a field no
    # code consumes, and the instruction itself tells the model to return
    # manual/requires_clarification with steps=[] when nothing is testable.
    verification_criteria: list[str] = Field(default_factory=list, max_length=20)
    priority: Priority
    testability: Testability
    steps: list[GoalStep] = Field(max_length=15)
    may_mutate: bool = False
    coverage_notes: list[str] = Field(default_factory=list, max_length=20)


class ElementChoice(GoalContract):
    _selection_method: str = PrivateAttr(default="contextual_ai_selection")
    element_id: str | None = Field(description="Exactly one ID from the current observation, or null if unavailable or ambiguous.")
    explanation: str = Field(max_length=1000)


class CriterionAssessment(GoalContract):
    # No upper bound: the requirement decides how many acceptance criteria exist
    # and nothing upstream caps them, so any constant here is wrong. A fixed 19
    # made every requirement with 21+ criteria permanently unassessable --
    # assess_goal demands coverage of range(len(acceptance_criteria)), which such
    # a response can never satisfy while also parsing. assess_goal's own check
    # ("covered each acceptance criterion exactly once") is the real enforcer,
    # and it knows the actual count.
    criterion_index: int = Field(ge=0)
    status: Literal["satisfied", "violated", "inconclusive", "not_tested"]
    explanation: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(max_length=30, description="Existing observation IDs supporting this conclusion; no invented IDs.")


class GoalAssessment(GoalContract):
    summary: str = Field(min_length=1, max_length=3000)
    # Bounded by the requirement's criterion count, which assess_goal checks
    # exactly; a constant cap here only turns a long requirement into a parse
    # failure that names no cause.
    criteria: list[CriterionAssessment] = Field(min_length=1)


class VisualDiagnosis(GoalContract):
    kind: Literal["missing_test_data", "environment_issue", "possible_product_issue", "insufficient_evidence", "other"]
    observed_facts: list[str] = Field(min_length=1, max_length=8)
    reasoning: str = Field(min_length=1, max_length=2000)
    suggested_next_step: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=3)
