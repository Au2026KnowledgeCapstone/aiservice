"""Translate goal_executor's `Execution` into the protocol's `RequirementOutcome`.

Two real impedance mismatches to bridge, both one-way and lossy by design:

- `CriterionVerdict.cited_evidence_ids` must be ids the sink actually issued.
  The assessment cites observation ids (`o1`, `o2`, ...) instead — an
  observation is not itself stored evidence. `_evidence_by_observation` maps
  each observation id to the sink-issued id of its screenshot (when one was
  captured), so citations translate rather than leak an internal id.
- `engines.base.Classification` is a fixed five-value enum; the reference's
  actual vocabulary (`goal_satisfied`, `potential_requirement_discrepancy`,
  `invalid_action_contract`, `possible_missing_test_data`, ...) is much
  richer. That richness isn't lost — it's still in `observed_behavior` — but
  the structured `classification` field necessarily collapses onto the five
  values the protocol defines. This is the fixed taxonomy A0 already locked
  in, not something this module should try to widen.
"""
from engines.base import ActionRecord, CriterionVerdict, RequirementOutcome, RequirementSnapshot
from engines.mvp.execution import Execution
from engines.mvp.requirement import criterion_id_at

_VERDICT = {"PASS": "pass", "FAIL": "fail", "INCONCLUSIVE": "inconclusive", "NOT_TESTED": "inconclusive"}
_CRITERION_VERDICT = {"satisfied": "pass", "violated": "fail", "inconclusive": "inconclusive", "not_tested": "inconclusive"}
_CLASSIFICATION = {
    "goal_satisfied": "criteria_satisfied",
    "potential_requirement_discrepancy": "criteria_violated",
    "partial_coverage": "execution_error",
    "missing_coverage": "execution_error",
    "policy_blocked": "environment_failure",
    "environment_failure": "environment_failure",
    "assessment_failure": "execution_error",
    "ai_execution_failure": "execution_error",
    "invalid_action_contract": "locator_failure",
    "action_result_unverified": "locator_failure",
    "possible_missing_test_data": "execution_error",
}


def _evidence_by_observation(execution: Execution) -> dict:
    return {
        entry["observation_id"]: entry["screenshot_key"]
        for entry in execution.logs
        if entry.get("type") == "observation" and entry.get("screenshot_captured") and entry.get("screenshot_key")
    }


def _criterion_verdicts(requirement: RequirementSnapshot, execution: Execution, evidence_by_observation: dict) -> tuple:
    assessment_log = next((entry for entry in execution.logs if entry.get("type") == "goal_assessment"), None)
    if assessment_log is None:
        # Execution never reached assessment (e.g. an environment failure before any plan ran).
        return tuple(
            CriterionVerdict(criterion_id=criterion.id, verdict="inconclusive", rationale=execution.observed_behavior, cited_evidence_ids=(), source="engine")
            for criterion in requirement.criteria
        )
    verdicts = []
    for criterion in assessment_log["assessment"]["criteria"]:
        cited = tuple(evidence_by_observation[ref] for ref in criterion["evidence_ids"] if ref in evidence_by_observation)
        verdicts.append(CriterionVerdict(
            criterion_id=criterion_id_at(requirement, criterion["criterion_index"]),
            verdict=_CRITERION_VERDICT.get(criterion["status"], "inconclusive"),
            rationale=criterion["explanation"], cited_evidence_ids=cited, source="ai",
        ))
    return tuple(verdicts)


def _actions(execution: Execution, evidence_by_observation: dict) -> tuple:
    records = []
    for entry in execution.logs:
        if "action" not in entry:
            continue
        evidence_id = evidence_by_observation.get(entry.get("after_observation_id")) or evidence_by_observation.get(entry.get("before_observation_id"))
        records.append(ActionRecord(
            index=entry["step_index"], kind=entry["action"], description=entry.get("intent", ""),
            outcome="completed" if entry.get("status") == "passed" else "stopped", evidence_id=evidence_id,
        ))
    return tuple(records)


def to_requirement_outcome(requirement: RequirementSnapshot, execution: Execution) -> RequirementOutcome:
    evidence_by_observation = _evidence_by_observation(execution)
    return RequirementOutcome(
        verdict=_VERDICT.get(execution.outcome, "inconclusive"),
        classification=_CLASSIFICATION.get(execution.classification, "execution_error"),
        observed_behavior=execution.observed_behavior,
        criterion_verdicts=_criterion_verdicts(requirement, execution, evidence_by_observation),
        actions=_actions(execution, evidence_by_observation),
    )
