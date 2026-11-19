"""choose_element's post-hoc element ID validation.

Deliberately not in test_mvp_planner.py: that module is gated behind
`RUN_BROWSER_CAPABILITY_TEST` because every test in it drives a real browser.
`choose_element` takes a plain provider, step and observation dict, so these
run always, with no browser and no AI spend.
"""
import json

import pytest

from engines.mvp.contracts import EngineConfiguration
from engines.mvp.errors import EngineError
from engines.mvp.goals import GoalStep
from engines.mvp.planner import choose_element


class SequenceProvider:
    """Returns a fixed sequence of responses and records every call it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def structured(self, *, operation, system, user, schema, images=()):
        self.calls.append({"operation": operation, "system": system, "user": json.loads(user), "schema": schema})
        if not self.responses:
            raise AssertionError("choose_element made more AI calls than the test supplied responses for.")
        return self.responses.pop(0)


def control(index, name):
    return {
        "element_id": f"obs-1-e{index}",
        "name": name,
        "role": "link",
        "actionable": True,
        "locator": {"test_id": f"product-{index}"},
        "scope": [{"name": "Search results"}],
    }


# Three plausible candidates, none of which the cheap name/scope heuristic can
# match uniquely, so choose_element is forced down its AI branch.
CONTROLS = [
    control(0, "HP Laptop 15 inch 8GB RAM"),
    control(1, "HP Laptop 14 inch 16GB RAM"),
    control(2, "Dell Inspiron 15 inch"),
]
SHORTLIST_IDS = ["obs-1-e0", "obs-1-e1", "obs-1-e2"]


@pytest.fixture
def observation():
    return {"observation_id": "obs-1", "url": "https://example.test/search", "controls": CONTROLS, "content_regions": []}


@pytest.fixture
def step():
    return GoalStep(
        action="click",
        purpose="activate",
        intent="open the HP laptop product page",
        target_description="HP Laptop product entry in the results list",
        expected_change="content",
    )


async def test_ai_branch_is_reached_and_shortlist_is_sent(step, observation):
    """Guards the fixtures themselves: if the cheap heuristic ever matches
    uniquely, the tests below would pass without exercising the AI path."""
    provider = SequenceProvider({"element_id": "obs-1-e0", "explanation": "First HP laptop result."})
    choice = await choose_element(provider, step, observation)
    assert len(provider.calls) == 1
    assert [item["element_id"] for item in provider.calls[0]["user"]["current_page"]["controls"]] == SHORTLIST_IDS
    assert choice.element_id == "obs-1-e0"
    assert choice._selection_method == "contextual_ai_selection"


async def test_schema_sent_to_provider_constrains_element_id_to_the_shortlist(step, observation):
    """Prevention: the model is handed an enum of the real IDs, so OpenAI's
    strict structured-output mode cannot generate an ID outside it."""
    provider = SequenceProvider({"element_id": "obs-1-e1", "explanation": "Second HP laptop result."})
    await choose_element(provider, step, observation)

    schema = provider.calls[0]["schema"]
    element_id = schema["properties"]["element_id"]
    enum_branch = next(branch for branch in element_id["anyOf"] if branch.get("type") == "string")
    assert enum_branch["enum"] == SHORTLIST_IDS
    assert {"type": "null"} in element_id["anyOf"], "Abstaining with element_id=null must stay possible."
    # The operation name drives ai_usage accounting and must not drift.
    assert provider.calls[0]["operation"] == "ElementChoice"


async def test_out_of_shortlist_choice_abstains_instead_of_failing_the_step(step, observation):
    """A provider that ignores the schema must not blow up the whole step."""
    provider = SequenceProvider({"element_id": "obs-1-e99", "explanation": "A control that is not on this page."})
    choice = await choose_element(provider, step, observation)

    assert choice.element_id is None
    assert choice._selection_method == "ambiguous"


async def test_out_of_shortlist_choice_does_not_trigger_a_second_ai_call(step, observation):
    """Every attempt is a real priced call. Rejecting a bad answer must stay free."""
    provider = SequenceProvider({"element_id": "obs-1-e99", "explanation": "A control that is not on this page."})
    await choose_element(provider, step, observation)
    assert len(provider.calls) == 1


async def test_explicit_null_choice_still_abstains(step, observation):
    provider = SequenceProvider({"element_id": None, "explanation": "No result clearly matches."})
    choice = await choose_element(provider, step, observation)
    assert choice.element_id is None


# --- Run #3: a navigate step compiled against the header search box ------------
# Mirrors the Amazon-Clone home page as captured in that run's own screenshot:
# one search textbox in the header plus product cards that are real <a href>
# links. Of every actionable control the engine observed there, the search box
# was the only one whose role falls outside compile_action's navigation set.

SEARCH_BOX = {
    "element_id": "obs-2-e0", "element": "input", "role": "textbox", "name": "Search products",
    "placeholder": "Search products", "actionable": True, "editable": True, "has_form": False,
    "locator": {"role": "textbox", "name": "Search products"}, "scope": [], "purpose": "view_filter",
}
PRODUCT_LINKS = [
    {
        "element_id": f"obs-2-e{index}", "element": "a", "role": "link", "name": name,
        "actionable": True, "href": f"/products/product/p{index}",
        "locator": {"role": "link", "name": name}, "scope": [{"role": "region", "name": "Products"}],
        "purpose": "navigation",
    }
    for index, name in enumerate(["HP Laptop 11 reviews $600", "Lenovo Laptop 25 reviews $580", "Mouse 46 reviews $350"], start=1)
]


def navigate_step():
    return GoalStep(
        action="click", purpose="navigate", intent="open the product page to add it to the cart",
        target_description="Select the product to add to cart.", expected_change="url",
    )


async def test_search_box_is_not_offered_for_a_navigation_step():
    """The production failure: the search box reached the shortlist, the model
    picked it, and compile_action rejected it one call later."""
    provider = SequenceProvider({"element_id": "obs-2-e1", "explanation": "First product link."})
    observation = {"observation_id": "obs-2", "url": "https://shop.test/", "controls": [SEARCH_BOX, *PRODUCT_LINKS], "content_regions": []}

    choice = await choose_element(provider, navigate_step(), observation)

    offered = [item["element_id"] for item in provider.calls[0]["user"]["current_page"]["controls"]]
    assert SEARCH_BOX["element_id"] not in offered
    assert offered == [link["element_id"] for link in PRODUCT_LINKS]
    # The enum the model is constrained to must not contain it either.
    enum = next(b for b in provider.calls[0]["schema"]["properties"]["element_id"]["anyOf"] if b.get("type") == "string")["enum"]
    assert SEARCH_BOX["element_id"] not in enum
    assert choice.element_id == "obs-2-e1"


async def test_navigation_step_abstains_when_only_text_fields_are_observed():
    """With nothing compilable on the page, abstain without spending a call."""
    provider = SequenceProvider()  # any AI call at all would raise
    observation = {"observation_id": "obs-2", "url": "https://shop.test/", "controls": [SEARCH_BOX], "content_regions": []}

    choice = await choose_element(provider, navigate_step(), observation)

    assert choice.element_id is None
    assert choice._selection_method == "unavailable"
    assert provider.calls == []


async def test_compile_action_still_rejects_a_text_field_for_navigation():
    """The guard this bug was blamed on is correct and stays exactly as strict."""
    from engines.mvp.action_contracts import compile_action
    from engines.mvp.errors import ActionContractError

    observation = {"observation_id": "obs-2", "url": "https://shop.test/", "controls": [SEARCH_BOX], "content_regions": []}
    with pytest.raises(ActionContractError, match="Navigation requires an observed navigation button or link"):
        compile_action(navigate_step(), SEARCH_BOX, observation, [], {}, EngineConfiguration(), "https://shop.test/", 0)


async def test_text_entry_step_still_offers_the_search_box():
    """The filter must not overreach: the same control is valid for entering text."""
    provider = SequenceProvider({"element_id": "obs-2-e0", "explanation": "The search field."})
    # Deliberately vague so the cheap name heuristic cannot match it outright
    # and the control has to survive filtering all the way into the shortlist.
    step = GoalStep(
        action="fill", purpose="enter_text", intent="search for the product",
        target_description="the box at the top of the page", value="HP Laptop", expected_change="content",
    )
    observation = {"observation_id": "obs-2", "url": "https://shop.test/", "controls": [SEARCH_BOX, *PRODUCT_LINKS], "content_regions": []}

    choice = await choose_element(provider, step, observation)

    offered = [item["element_id"] for item in provider.calls[0]["user"]["current_page"]["controls"]]
    assert offered == [SEARCH_BOX["element_id"]]
    assert choice.element_id == "obs-2-e0"


async def test_activate_click_on_a_text_field_is_still_offered():
    """compile_action silently coerces activate+click on a text field to focus,
    so selection must not filter it out ahead of that coercion."""
    provider = SequenceProvider({"element_id": "obs-2-e0", "explanation": "Focus the search field."})
    step = GoalStep(
        action="click", purpose="activate", intent="focus the search field",
        target_description="Search products field", expected_change="none",
    )
    observation = {"observation_id": "obs-2", "url": "https://shop.test/", "controls": [SEARCH_BOX], "content_regions": []}

    choice = await choose_element(provider, step, observation)
    assert choice.element_id == "obs-2-e0"


def test_selectable_matches_compile_actions_role_sets():
    """Drift guard: selection and compilation must read the same rules."""
    from engines.mvp import action_contracts as contracts

    assert contracts.selectable(navigate_step(), SEARCH_BOX) is False
    assert all(contracts.selectable(navigate_step(), link) for link in PRODUCT_LINKS)
    for role in contracts.NAVIGATION_ROLES:
        assert contracts.selectable(navigate_step(), {"role": role}) is True
    toggle = GoalStep(action="check", purpose="set_checked", intent="tick it", target_description="a box", checked=True)
    assert contracts.selectable(toggle, {"role": "checkbox"}) is True
    assert contracts.selectable(toggle, {"role": "link"}) is False


def test_shortlist_schema_survives_strict_mode_normalization():
    """The enum has to still be there after ai_transport rewrites the schema for
    strict mode, and the root must stay an object (OpenAI forbids anyOf at root)."""
    from app.ai_transport import OpenAITransport

    from engines.mvp.planner import _shortlist_schema

    strict = OpenAITransport._strict_schema(_shortlist_schema(SHORTLIST_IDS))

    assert strict["type"] == "object"
    assert strict["additionalProperties"] is False
    assert sorted(strict["required"]) == ["element_id", "explanation"]
    branches = strict["properties"]["element_id"]["anyOf"]
    assert next(b for b in branches if b.get("type") == "string")["enum"] == SHORTLIST_IDS
    assert {"type": "null"} in branches


async def test_malformed_response_still_raises(step, observation):
    """D reclassifies the out-of-shortlist case only. A response that does not
    match the schema at all is still a hard error, per engines/mvp/ai.py."""
    provider = SequenceProvider({"explanation": "Missing the element_id field entirely."})
    with pytest.raises(EngineError, match="did not match the required schema"):
        await choose_element(provider, step, observation)
