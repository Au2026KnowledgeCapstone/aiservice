"""Step 3 checkpoint: a changed/ambiguous control is refused, never clicked; a
postcondition mismatch raises rather than silently passing as verified."""
import pytest

from engines.mvp.action_contracts import ActionContractError, compile_action, verify_action
from engines.mvp.contracts import EngineConfiguration
from engines.mvp.goals import GoalStep
from engines.mvp.requirement import as_dict
from engines.base import CriterionSnapshot, RequirementSnapshot

CONFIG = EngineConfiguration()
TARGET_URL = "https://example.test/"
REQUIREMENT = as_dict(RequirementSnapshot(
    id="req-1", title="Login", description="User can submit the form",
    criteria=(CriterionSnapshot(id="c1", text="Submitting works"),),
))


def actionable_button(name="Submit", **overrides):
    control = {
        "element_id": "obs-1-e0", "role": "button", "name": name, "disabled": False,
        "blocked_by_modal": False, "actionable": True, "ambiguous": False,
        "locator": {"role": "button", "name": name}, "scope": [], "has_form": False,
        "input_type": "button", "editable": False, "readonly": False, "options": [],
        "checked": None, "expanded": None, "in_popup": False, "href": None,
    }
    control.update(overrides)
    return control


def observation(controls, **overrides):
    obs = {
        "observation_id": "obs-1", "url": TARGET_URL, "controls": controls,
        "content_regions": [], "interaction_context": {"popups": []},
    }
    obs.update(overrides)
    return obs


def test_activate_compiles_against_an_available_control():
    step = GoalStep(action="click", purpose="activate", intent="submit the form", expected_change="content")
    control = actionable_button()
    bound = compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)
    assert bound.step.action == "click"
    assert bound.postcondition == "content_changed"


def test_ambiguous_control_is_refused_not_clicked():
    # discovery.py marks a duplicate-identity control actionable=False; compile_action
    # must refuse it outright rather than clicking the ambiguous element.
    step = GoalStep(action="click", purpose="activate", intent="submit the form", expected_change="content")
    control = actionable_button(actionable=False, ambiguous=True)
    with pytest.raises(ActionContractError):
        compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)


def test_disabled_control_is_refused_not_clicked():
    step = GoalStep(action="click", purpose="activate", intent="submit the form", expected_change="content")
    control = actionable_button(disabled=True)
    with pytest.raises(ActionContractError):
        compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)


class FakeSession:
    def __init__(self, consistent=True):
        self.consistent = consistent

    async def resolve_element(self, element_id):
        raise AssertionError("not needed for a destination_url postcondition")

    async def capture_is_consistent(self, page, observation):
        return self.consistent


class FakePage:
    def __init__(self, url):
        self.url = url


async def test_verify_action_rejects_unverified_evidence_integrity():
    step = GoalStep(action="navigate", purpose="navigate", intent="go to dashboard", url="/dashboard", expected_change="url")
    control = actionable_button(role="link", name="Dashboard", href="/dashboard")
    bound = compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)
    after = observation([], url="https://example.test/dashboard", evidence_integrity="unstable", accessible_ui="some text", content_images=[])
    with pytest.raises(ActionContractError):
        await verify_action(bound, FakeSession(), FakePage("https://example.test/dashboard"), after, observation([control]))


async def test_verify_action_rejects_destination_mismatch():
    step = GoalStep(action="navigate", purpose="navigate", intent="go to dashboard", url="/dashboard", expected_change="url")
    control = actionable_button(role="link", name="Dashboard", href="/dashboard")
    bound = compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)
    # The browser stayed on a different page than the one the action contract expected.
    after = observation([], url="https://example.test/somewhere-else", evidence_integrity="verified", accessible_ui="some text", content_images=[])
    with pytest.raises(ActionContractError):
        await verify_action(bound, FakeSession(), FakePage("https://example.test/somewhere-else"), after, observation([control]))


async def test_verify_action_accepts_a_consistent_destination_match():
    step = GoalStep(action="navigate", purpose="navigate", intent="go to dashboard", url="/dashboard", expected_change="url")
    control = actionable_button(role="link", name="Dashboard", href="/dashboard")
    bound = compile_action(step, control, observation([control]), [], REQUIREMENT, CONFIG, TARGET_URL, 0)
    after = observation([], url="https://example.test/dashboard", evidence_integrity="verified", accessible_ui="Dashboard", content_images=[])
    result = await verify_action(bound, FakeSession(), FakePage("https://example.test/dashboard"), after, observation([control]))
    assert result["status"] == "verified"
