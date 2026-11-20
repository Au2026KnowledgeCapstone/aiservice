"""GoalStep's parse-time contract, and what a schema violation reports.

Always on: this is pure model validation, no browser and no AI spend. The
fixtures below are real responses captured from gpt-4o-mini planning against
the live Amazon-Clone target, not invented shapes.
"""
import json

import pytest
from pydantic import BaseModel, ValidationError

from engines.mvp import ai
from engines.mvp.errors import EngineError
from engines.mvp.goals import GoalPlan, GoalStep


def plan_with(step):
    return {
        "objective": "Add a product to the shopping cart",
        "preconditions": [], "expected_behavior": "The product appears in the cart",
        "verification_criteria": ["The cart shows the product"],
        "priority": "medium", "testability": "automatable",
        "steps": [step], "may_mutate": False, "coverage_notes": [],
    }


# Captured verbatim: the model reaches for a button with `press` and supplies no
# key. compile_action replaces both action and value for a submit step anyway.
CAPTURED_SUBMIT_PRESS = {
    "action": "press", "purpose": "submit", "intent": "submit the cart to checkout",
    "target_description": "Checkout button", "value": None, "expected_change": "url",
}


def test_submit_press_without_a_key_is_accepted():
    """Regression: this exact step failed the whole run with "AI response did not
    match the required schema for GoalPlan", losing all six acceptance criteria."""
    plan = GoalPlan.model_validate(plan_with(CAPTURED_SUBMIT_PRESS))

    step = plan.steps[0]
    assert step.action == "press"
    assert step.value is None, "compile_action supplies the key for a submit step"


def test_keyboard_press_without_a_key_is_still_rejected():
    """The exemption is scoped to submit. A real keyboard step needs its key:
    compile_action reads step.value there and checks it against the requirement."""
    with pytest.raises(ValidationError, match="Action requires value"):
        GoalStep.model_validate({**CAPTURED_SUBMIT_PRESS, "purpose": "keyboard"})


def test_unsupported_key_is_still_rejected_even_on_a_submit_step():
    with pytest.raises(ValidationError, match="Unsupported key"):
        GoalStep.model_validate({**CAPTURED_SUBMIT_PRESS, "value": "F13"})


@pytest.mark.parametrize("action", ["fill", "select"])
def test_fill_and_select_still_require_a_value(action):
    with pytest.raises(ValidationError, match="Action requires value"):
        GoalStep.model_validate({
            "action": action, "purpose": "submit", "intent": "type the search term",
            "target_description": "Search products", "value": None, "expected_change": "content",
        })


# Captured verbatim: the model put the destination in `value` and left `url`
# null. The engine can still ground a destination without step.url, so this is
# compile_action's call to make, not the parser's.
CAPTURED_NAVIGATE_NO_URL = {
    "action": "navigate", "purpose": "navigate", "destination_label": "Products",
    "intent": "Open the products available on the homepage.", "target_description": None,
    "expected_change": "url", "value": "https://shop.test/", "url": None,
}


def test_navigate_without_a_url_parses_and_is_left_to_the_compiler():
    """Regression: this cost an entire run at parse time. One unusable step must
    not discard the other fourteen, nor the assessment of every criterion."""
    plan = GoalPlan.model_validate(plan_with(CAPTURED_NAVIGATE_NO_URL))
    assert plan.steps[0].url is None


def test_the_compiler_still_refuses_an_ungrounded_navigate():
    """The rule did not disappear, it moved to where the page is known -- and it
    now fails one step with a reason instead of the whole plan."""
    from engines.mvp.action_contracts import compile_action
    from engines.mvp.contracts import EngineConfiguration
    from engines.mvp.errors import ActionContractError

    step = GoalPlan.model_validate(plan_with(CAPTURED_NAVIGATE_NO_URL)).steps[0]
    observation = {"observation_id": "o1", "url": "https://shop.test/", "controls": [], "content_regions": []}

    with pytest.raises(ActionContractError, match="grounded destination URL"):
        compile_action(step, None, observation, [], {}, EngineConfiguration(), "https://shop.test/", 0)


class BadProvider:
    def __init__(self, response):
        self.response = response

    async def structured(self, *, operation, system, user, schema, images=()):
        return self.response


async def test_schema_failure_names_the_field_and_the_rule():
    """"did not match the required schema" alone cannot be acted on: a fifteen
    step plan gives no clue which step was wrong."""
    broken = plan_with({**CAPTURED_SUBMIT_PRESS, "purpose": "keyboard"})

    with pytest.raises(EngineError) as caught:
        await ai.structured(BadProvider(broken), "plan it", {}, GoalPlan)

    message = str(caught.value)
    assert "GoalPlan" in message
    assert "steps.0" in message
    assert "Action requires value" in message


async def test_schema_failure_does_not_echo_the_offending_content():
    """The message reaches an end user, so it carries field paths and rule text
    only -- never the model's output or anything read off the page."""
    class Secret(BaseModel):
        count: int

    with pytest.raises(EngineError) as caught:
        await ai.structured(BadProvider({"count": "sensitive-page-text"}), "x", {}, Secret)

    assert "sensitive-page-text" not in str(caught.value)
    assert "count" in str(caught.value)


def test_the_captured_plans_from_production_now_validate():
    """Both plans sampled live against Amazon-Clone failed only on this rule."""
    for index in (1, 6):
        plan = plan_with(CAPTURED_SUBMIT_PRESS)
        plan["steps"] = [
            {"action": "click", "purpose": "navigate", "intent": "open the product",
             "target_description": "HP Laptop", "expected_change": "url"}
        ] * index + [CAPTURED_SUBMIT_PRESS]
        assert len(GoalPlan.model_validate(json.loads(json.dumps(plan))).steps) == index + 1


# --- Bounds that belong to the requirement, not to a constant ------------------

def assessment_criterion(index):
    return {"criterion_index": index, "status": "satisfied", "explanation": "ok", "evidence_ids": []}


def test_a_requirement_with_more_than_twenty_criteria_can_be_assessed():
    """Regression, and deterministic rather than probabilistic: `criteria` was
    capped at 20 items and `criterion_index` at 19, while nothing upstream caps
    acceptance criteria at all (FE `z.array(...)`, BE `list[AcceptanceCriterion]`).
    assess_goal demands coverage of range(len(acceptance_criteria)), so any
    requirement with 21+ criteria could never produce a response that both
    parses and covers -- every run on it failed, every time."""
    from engines.mvp.goals import GoalAssessment

    assessment = GoalAssessment.model_validate(
        {"summary": "done", "criteria": [assessment_criterion(i) for i in range(25)]}
    )

    assert len(assessment.criteria) == 25
    assert max(c.criterion_index for c in assessment.criteria) == 24


def test_a_negative_criterion_index_is_still_rejected():
    """Only the arbitrary ceiling went; an index that cannot address any
    criterion is still refused."""
    from engines.mvp.goals import GoalAssessment

    with pytest.raises(ValidationError):
        GoalAssessment.model_validate({"summary": "x", "criteria": [assessment_criterion(-1)]})


def test_an_empty_assessment_is_still_rejected():
    from engines.mvp.goals import GoalAssessment

    with pytest.raises(ValidationError):
        GoalAssessment.model_validate({"summary": "x", "criteria": []})


def test_a_plan_with_no_verification_criteria_is_accepted():
    """Nothing in engines/ or app/ reads verification_criteria -- it is plan
    context for the assessor. Losing an entire run over an empty list was a
    rejection with no beneficiary, and the instruction explicitly tells the model
    to return manual/requires_clarification with steps=[] when nothing is
    testable."""
    plan = plan_with(CAPTURED_SUBMIT_PRESS)
    plan["verification_criteria"] = []

    assert GoalPlan.model_validate(plan).verification_criteria == []


# --- activate steps must carry an observable change ---------------------------

class PlanProvider:
    def __init__(self, plan):
        self.plan = plan

    async def structured(self, *, operation, system, user, schema, images=()):
        return self.plan


def observation_for_plan():
    return {"observation_id": "o1", "url": "https://shop.test/", "controls": [], "content_regions": []}


async def plan_from(step):
    from engines.mvp.planner import generate_plan

    return await generate_plan(PlanProvider(plan_with(step)), {}, observation_for_plan(), "https://shop.test/")


ADD_TO_CART = {
    "action": "click", "purpose": "activate", "intent": "add the HP Laptop to the shopping cart",
    "target_description": "Add to cart button on the product detail page", "expected_change": "none",
}


async def test_an_activate_step_declaring_no_change_is_normalised_to_content():
    """Regression, production run #6: the model planned Add to Cart as activate
    with expected_change=none, compile_action refused the contract, and the click
    never happened -- all six criteria came back inconclusive. `none` is never
    usable for an explicit activate step (objective_checks is always empty here),
    so the plan was unrunnable either way; normalising makes it testable."""
    plan = await plan_from(ADD_TO_CART)

    assert plan.steps[0].expected_change == "content"


async def test_a_normalised_activate_step_now_compiles():
    """The point of the normalisation: the step reaches the page instead of
    being refused before the click."""
    from engines.mvp.action_contracts import compile_action
    from engines.mvp.contracts import EngineConfiguration

    plan = await plan_from(ADD_TO_CART)
    control = {"element_id": "o1-e0", "role": "button", "name": "Add to Cart",
               "actionable": True, "locator": {"role": "button", "name": "Add to Cart"}, "scope": []}
    observation = {**observation_for_plan(), "controls": [control]}

    bound = compile_action(plan.steps[0], control, observation, [], {}, EngineConfiguration(), "https://shop.test/", 0)

    assert bound.purpose == "activate"
    assert bound.postcondition == "content_changed", "the change is now actually verified after the click"


async def test_an_explicit_url_expectation_is_left_alone():
    plan = await plan_from({**ADD_TO_CART, "expected_change": "url"})
    assert plan.steps[0].expected_change == "url"


async def test_inspect_steps_keep_none():
    """Only activate is normalised: a read has nothing to change."""
    plan = await plan_from({
        "action": "read", "purpose": "inspect", "intent": "read the cart total",
        "target_description": "Cart subtotal", "expected_change": "none",
    })
    assert plan.steps[0].expected_change == "none"
