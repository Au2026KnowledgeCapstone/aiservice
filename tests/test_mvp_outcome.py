"""`to_requirement_outcome` must emit one ActionRecord per executed step.

BE rejects a requirement outcome whose actions repeat an index (409, non-retryable),
which strands the whole job's callback sequence. Diagnostic log entries carry the
failing step's `action`/`step_index` on purpose, so the translation has to tell them
apart from genuine per-step entries.
"""
from engines.base import CriterionSnapshot, RequirementSnapshot
from engines.mvp.diagnostics import failure_point
from engines.mvp.execution import Execution
from engines.mvp.outcome import to_requirement_outcome

REQUIREMENT = RequirementSnapshot(
    id="req-1", title="Login", description="A user can log in.",
    criteria=[CriterionSnapshot(id="crit-1", text="The dashboard is shown.")],
)


def _action_step(index=0):
    """The one entry shape goal_executor appends per plan step (no `type` marker)."""
    return {
        "step_index": index, "action": "click", "intent": "Initiate the login process",
        "timestamp": "2026-11-17T00:00:00+00:00", "status": "running",
        "before_observation_id": "o1", "after_observation_id": "o2",
    }


def _observation(observation_id, evidence_id):
    return {
        "type": "observation", "observation_id": observation_id, "screenshot_captured": True,
        "screenshot_key": evidence_id, "url": "https://example.test/login", "accessible_ui": "",
    }


def test_failure_point_does_not_duplicate_its_step_s_action():
    step = _action_step()
    step["status"] = "inconclusive"
    point = failure_point(
        stage="state_transition", reason="The browser action could not be completed.",
        expected="The dashboard is shown.", step=0, event=step,
        observations=[_observation("o1", "ev-1")],
    )
    execution = Execution(logs=[_observation("o1", "ev-1"), step, point])

    actions = to_requirement_outcome(REQUIREMENT, execution).actions

    assert len(actions) == 1, actions
    assert len({action.index for action in actions}) == len(actions)
    assert actions[0].index == 0
    assert actions[0].kind == "click"
    assert actions[0].outcome == "stopped"
    assert actions[0].evidence_id == "ev-1"


def test_every_executed_step_is_still_reported():
    execution = Execution(logs=[
        _observation("o1", "ev-1"), _observation("o2", "ev-2"),
        {**_action_step(0), "status": "passed"},
        {"type": "verification_context", "step_index": 0, "status": "observed", "observation_id": "o2"},
        {**_action_step(1), "action": "fill", "status": "passed"},
    ])

    actions = to_requirement_outcome(REQUIREMENT, execution).actions

    assert [(action.index, action.kind, action.outcome) for action in actions] == [
        (0, "click", "completed"), (1, "fill", "completed"),
    ]
    assert actions[0].evidence_id == "ev-2"
