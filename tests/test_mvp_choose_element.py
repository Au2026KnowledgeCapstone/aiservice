"""choose_element's post-hoc element ID validation.

Deliberately not in test_mvp_planner.py: that module is gated behind
`RUN_BROWSER_CAPABILITY_TEST` because every test in it drives a real browser.
`choose_element` takes a plain provider, step and observation dict, so these
run always, with no browser and no AI spend.
"""
import json

import pytest

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
